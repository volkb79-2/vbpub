#!/usr/bin/env python3
"""Verify and publish the B110 pilot completion marker from pinned artifacts."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEADER = re.compile(
    rb"# b110 pilot selection seed=b110-pilot-2026 size=64 plan_candidates=[0-9]+\n\Z"
)
_WINDOW_NS = 90 * 60 * 1_000_000_000
_FILE_LIMIT = 256 * 1024 * 1024
_SNAPSHOT_NAME = "b110-pilot-evidence"
_SNAPSHOT_INDEX = "snapshot.sha256"
_SNAPSHOT_RECEIPT = "b110-pilot-evidence.receipt.json"
_SNAPSHOT_ATTESTATION = "b110-pilot-evidence.attestation.json"
_ARCHIVED_RUN_GATE_TRANSCRIPT = "b110-pilot-run-gate.log"
_SNAPSHOT_PENDING = "b110-pilot-evidence.pending.json"
_SNAPSHOT_INCOMPLETE = "b110-pilot-evidence-incomplete"
_SNAPSHOT_UNVERIFIED_FALLBACK = "b110-pilot-evidence-unverified"
_INNER_MANIFEST = "b110-pilot-artifacts.sha256"
_RUN_GATE_HISTORY_MAX = 4 * 1024 * 1024
_RUN_GATE_LOG_MAX = 128 * 1024 * 1024
_RUN_GATE_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")

try:
    _RENAMEAT2 = ctypes.CDLL(None, use_errno=True).renameat2
except AttributeError:
    _RENAMEAT2 = None
else:
    _RENAMEAT2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    _RENAMEAT2.restype = ctypes.c_int
_RENAME_NOREPLACE = 1


class HistoryUnavailableError(RuntimeError):
    """Run-gate evidence cannot be checked yet; preserve live evidence for retry."""


class NoSuccessfulAttestationError(RuntimeError):
    """Readable run-gate history contains no successful attestation for this snapshot."""


class PriorEvidenceMismatch(ValueError):
    """A valid successful transcript proves the retained receipt was changed."""


def _check_expected_assay_identity(args: argparse.Namespace, assay_info: os.stat_result) -> None:
    expected_device = getattr(args, "expected_assay_device", None)
    expected_inode = getattr(args, "expected_assay_inode", None)
    expected_absent = getattr(args, "expected_assay_absent", False)
    if expected_absent:
        raise ValueError("published pilot evidence requires the prepared .assay directory identity")
    if expected_device is None and expected_inode is None:
        return
    if (
        type(expected_device) is not int
        or expected_device < 0
        or type(expected_inode) is not int
        or expected_inode < 1
    ):
        raise ValueError("prepared .assay directory identity is malformed")
    if (assay_info.st_dev, assay_info.st_ino) != (expected_device, expected_inode):
        raise ValueError(".assay directory changed after B110 launcher admission")


_STRUCTURAL_PATH_ERRNOS = {
    errno.ENOENT,
    errno.ENOTDIR,
    errno.EISDIR,
    errno.ELOOP,
    errno.ENAMETOOLONG,
}


def _raise_filesystem_error(exc: OSError, *, label: str) -> None:
    if exc.errno in _STRUCTURAL_PATH_ERRNOS:
        raise ValueError(f"{label}: {exc}") from exc
    raise HistoryUnavailableError(f"{label} is unavailable: {exc}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _open_regular(directory_fd: int, name: str, *, label: str, limit: int) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        _raise_filesystem_error(exc, label=f"cannot open {label} without following links")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"{label} is not a single-link regular file")
        if info.st_size > limit:
            raise ValueError(f"{label} exceeds the {limit}-byte limit")
        return descriptor, info
    except BaseException:
        os.close(descriptor)
        raise


def _read_fd(descriptor: int, *, limit: int, label: str) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    content = bytearray()
    while len(content) <= limit:
        block = os.read(descriptor, min(1024 * 1024, limit + 1 - len(content)))
        if not block:
            break
        content.extend(block)
    if len(content) > limit:
        raise ValueError(f"{label} exceeds the {limit}-byte limit")
    return bytes(content)


def _hash_fd(descriptor: int, *, label: str, limit: int = _FILE_LIMIT) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        block = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
        if not block:
            break
        total += len(block)
        if total > limit:
            raise ValueError(f"{label} exceeds the {limit}-byte limit while hashing")
        digest.update(block)
    return digest.hexdigest()


def _parse_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} is not a JSON object")
    return document


def _timestamp(value: Any, label: str) -> int:
    if not isinstance(value, str):
        raise ValueError(f"{label} is not a UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"{label} is not a UTC timestamp") from exc
    elapsed = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    whole_seconds = elapsed.days * 86_400 + elapsed.seconds
    return whole_seconds * 1_000_000_000 + elapsed.microseconds * 1_000


def _verify_deadlines(
    *,
    deadline_raw: bytes,
    window_raw: bytes,
    campaign: str,
    commit: str,
    tree: str,
    require_active: bool = True,
) -> tuple[int, int]:
    deadline = _parse_json(deadline_raw, label="campaign deadline")
    expected_deadline_keys = {
        "schema", "campaign", "commit", "git_tree", "lanes", "assay_version",
        "wheel_sha256", "plan_sha256", "created_at_utc", "expires_at_utc",
    }
    if set(deadline) != expected_deadline_keys:
        raise ValueError("campaign deadline has missing or unknown fields")
    if (
        deadline.get("schema") != "assay-campaign-deadline/1"
        or deadline.get("campaign") != campaign
        or deadline.get("commit") != commit
        or deadline.get("git_tree") != tree
        or deadline.get("lanes") != ["self-qualification"]
        or not isinstance(deadline.get("git_tree"), str)
        or _HEX40.fullmatch(deadline["git_tree"]) is None
        or not isinstance(deadline.get("wheel_sha256"), str)
        or _HEX64.fullmatch(deadline["wheel_sha256"]) is None
        or not isinstance(deadline.get("assay_version"), str)
        or not deadline["assay_version"]
        or not isinstance(deadline.get("plan_sha256"), dict)
        or set(deadline["plan_sha256"]) != {"self-qualification"}
        or not isinstance(deadline["plan_sha256"]["self-qualification"], str)
        or _HEX64.fullmatch(deadline["plan_sha256"]["self-qualification"]) is None
    ):
        raise ValueError("campaign deadline does not bind the expected pilot and source")
    campaign_started = _timestamp(deadline.get("created_at_utc"), "campaign created_at_utc")
    campaign_expires = _timestamp(deadline.get("expires_at_utc"), "campaign expires_at_utc")
    if campaign_expires - campaign_started not in {7200 * 1_000_000_000, 7201 * 1_000_000_000}:
        raise ValueError("campaign deadline is not the declared two-hour window")

    window = _parse_json(window_raw, label="pilot attempt window")
    if set(window) != {
        "schema", "campaign", "commit", "started_at_epoch_ns", "expires_at_epoch_ns",
    }:
        raise ValueError("pilot attempt window has missing or unknown fields")
    started = window.get("started_at_epoch_ns")
    expires = window.get("expires_at_epoch_ns")
    if (
        window.get("schema") != "assay-b110-pilot-attempt-window/1"
        or window.get("campaign") != campaign
        or window.get("commit") != commit
        or type(started) is not int
        or type(expires) is not int
        or expires - started != _WINDOW_NS
    ):
        raise ValueError("pilot attempt window does not bind the expected 90-minute attempt")
    now_ns = time.time_ns()
    if require_active:
        if campaign_started > now_ns or campaign_expires <= now_ns:
            raise ValueError("two-hour campaign deadline is not active at host completion")
        if started > now_ns or expires <= now_ns:
            raise ValueError("90-minute pilot attempt window is not active at host completion")
    return campaign_expires, expires


def _candidate_ids(candidates_raw: bytes) -> set[str]:
    if not candidates_raw.endswith(b"\n"):
        raise ValueError("pilot candidate file ends with a partial record")
    header, *candidate_lines = candidates_raw.splitlines()
    if _HEADER.fullmatch(header + b"\n") is None or not candidate_lines:
        raise ValueError("pilot candidate file has an invalid header or empty selection")
    if len(candidate_lines) > 100_000:
        raise ValueError("pilot candidate file exceeds the candidate limit")
    candidate_ids: set[str] = set()
    for line in candidate_lines:
        try:
            identity = line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("pilot candidate file contains a non-ASCII candidate ID") from exc
        if _HEX64.fullmatch(identity) is None or identity in candidate_ids:
            raise ValueError("pilot candidate file contains an invalid or duplicate candidate ID")
        candidate_ids.add(identity)
    return candidate_ids


def _expected_artifacts(
    candidate_ids: set[str], commit: str, *, inventory_version: int = 2
) -> set[str]:
    deadline_name = f"campaign-deadline-b110-pilot-{commit[:12]}.json"
    expected = {
        ".assay/b110-pilot-plan.json",
        ".assay/b110-pilot-candidates.txt",
        ".assay/b110-pilot-selection.json",
        ".assay/b110-pilot-summary.json",
        ".assay/b110-pilot-run.log",
        ".assay/b110-pilot-attempt-window.json",
        ".assay/progress-b110-pilot.jsonl",
        f".assay/{deadline_name}",
        ".assay/b110-pilot-state/PILOT-STATE",
        *(f".assay/b110-pilot-state/{identity}.json" for identity in candidate_ids),
    }
    if inventory_version >= 2:
        expected.add(".assay/r2-manifest-b110-pilot.txt")
    return expected


def _parse_manifest(raw: bytes, expected: set[str], *, label: str) -> dict[str, str]:
    if not raw.endswith(b"\n"):
        raise ValueError(f"{label} ends with a partial record")
    entries: dict[str, str] = {}
    for number, line in enumerate(raw.splitlines(), start=1):
        match = re.fullmatch(rb"([0-9a-f]{64})  ([A-Za-z0-9_./-]+)", line)
        if match is None:
            raise ValueError(f"{label} line {number} is malformed")
        path = match.group(2).decode("ascii")
        if path not in expected:
            raise ValueError(f"{label} names an unexpected artifact: {path}")
        if path in entries:
            raise ValueError(f"{label} repeats artifact: {path}")
        entries[path] = match.group(1).decode("ascii")
    if set(entries) != expected:
        missing = sorted(expected - set(entries))
        extra = sorted(set(entries) - expected)
        raise ValueError(f"{label} does not cover the exact artifact set (missing={missing}, extra={extra})")
    return entries


def _directory_names(directory_fd: int, *, label: str) -> set[str]:
    try:
        names = set(os.listdir(directory_fd))
    except OSError as exc:
        _raise_filesystem_error(exc, label=f"cannot enumerate {label}")
    for name in names:
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            _raise_filesystem_error(exc, label=f"cannot inspect {label} entry {name!r}")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f"{label} entry {name!r} is not a real file or directory")
    return names


def _check_state_inventory(state_fd: int, candidate_ids: set[str], *, label: str) -> set[str]:
    expected = {"PILOT-STATE", *(f"{identity}.json" for identity in candidate_ids)}
    observed = _directory_names(state_fd, label=label)
    for name in observed:
        info = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"{label} entry {name!r} is not a single-link regular file")
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(f"{label} inventory differs (missing={missing}, extra={extra})")
    return expected


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while creating pilot evidence snapshot")
        view = view[written:]


def _create_snapshot_file(directory_fd: int, name: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"snapshot output {name!r} is not a single-link regular file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _copy_descriptor(
    source_fd: int,
    destination_fd: int,
    *,
    expected_digest: str,
    label: str,
    limit: int = _FILE_LIMIT,
) -> str:
    os.lseek(source_fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    copied = 0
    while True:
        block = os.read(source_fd, min(1024 * 1024, limit + 1 - copied))
        if not block:
            break
        copied += len(block)
        if copied > limit:
            raise ValueError(f"{label} exceeds the {limit}-byte limit while copying")
        digest.update(block)
        _write_all(destination_fd, block)
    os.fsync(destination_fd)
    actual = digest.hexdigest()
    if actual != expected_digest:
        raise ValueError(f"{label} changed while it was copied into the evidence snapshot")
    return actual


def _write_snapshot_bytes(
    directory_fd: int,
    name: str,
    content: bytes,
    *,
    expected_digest: str,
) -> None:
    descriptor = _create_snapshot_file(directory_fd, name)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise ValueError(f"snapshot {name!r} bytes differ from their attested digest")
    finally:
        os.close(descriptor)


def _remove_directory_contents(directory_fd: int) -> None:
    os.fchmod(directory_fd, 0o700)
    for name in os.listdir(directory_fd):
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                _remove_directory_contents(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _snapshot_paths(expected: set[str]) -> set[str]:
    return {path.removeprefix(".assay/") for path in expected} | {_INNER_MANIFEST}


def _snapshot_top_level(expected_snapshot: set[str]) -> set[str]:
    return {path.split("/", 1)[0] for path in expected_snapshot} | {_SNAPSHOT_INDEX}


def _snapshot_file_fd(snapshot_fd: int, state_fd: int, relative: str, *, label: str) -> tuple[int, os.stat_result]:
    if relative.startswith("b110-pilot-state/"):
        name = relative.split("/", 1)[1]
        return _open_regular(state_fd, name, label=label, limit=_FILE_LIMIT)
    return _open_regular(snapshot_fd, relative, label=label, limit=_FILE_LIMIT)


def _require_read_only(info: os.stat_result, *, label: str) -> None:
    expected_mode = 0o500 if stat.S_ISDIR(info.st_mode) else 0o400
    if (
        info.st_uid != os.geteuid()
        or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        or stat.S_IMODE(info.st_mode) != expected_mode
    ):
        raise ValueError(f"published pilot evidence is not owner-only read-only: {label}")


def _require_private_writable_directory(info: os.stat_result, *, label: str) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_mode & 0o300 != 0o300
    ):
        raise ValueError(f"pilot directory is not private and writable by the current user: {label}")


def _parse_snapshot_receipt(raw: bytes) -> dict[str, Any]:
    receipt = _parse_json(raw, label="published pilot snapshot receipt")
    expected_keys = {
        "schema", "campaign", "commit", "tree", "manifest_sha256", "snapshot_sha256",
    }
    if set(receipt) != expected_keys:
        raise ValueError("published pilot snapshot receipt has missing or unknown fields")
    if (
        receipt.get("schema") not in {
            "assay-b110-pilot-evidence-receipt/1",
            "assay-b110-pilot-evidence-receipt/2",
        }
        or not isinstance(receipt.get("commit"), str)
        or _HEX40.fullmatch(receipt["commit"]) is None
        or not isinstance(receipt.get("tree"), str)
        or _HEX40.fullmatch(receipt["tree"]) is None
        or receipt.get("campaign") != f"b110-pilot-{receipt['commit'][:12]}"
        or not isinstance(receipt.get("manifest_sha256"), str)
        or _HEX64.fullmatch(receipt["manifest_sha256"]) is None
        or not isinstance(receipt.get("snapshot_sha256"), str)
        or _HEX64.fullmatch(receipt["snapshot_sha256"]) is None
    ):
        raise ValueError("published pilot snapshot receipt does not bind a valid source and digest")
    return receipt


def _receipt_inventory_version(receipt: dict[str, Any]) -> int:
    schema = receipt.get("schema")
    if schema == "assay-b110-pilot-evidence-receipt/1":
        return 1
    if schema == "assay-b110-pilot-evidence-receipt/2":
        return 2
    raise ValueError("published pilot snapshot receipt has an unknown inventory version")


def _parse_snapshot_attestation(raw: bytes) -> dict[str, Any]:
    attestation = _parse_json(raw, label="published pilot run-gate attestation")
    if not isinstance(attestation, dict):
        raise ValueError("published pilot run-gate attestation is not an object")
    expected_keys = {
        "schema", "campaign", "commit", "tree", "manifest_sha256",
        "snapshot_sha256", "run_id", "log_path", "transcript_sha256",
        "archive_transcript_path",
    }
    if set(attestation) != expected_keys:
        raise ValueError("published pilot run-gate attestation has missing or unknown fields")
    if (
        attestation.get("schema") != "assay-b110-pilot-run-gate-attestation/2"
        or not isinstance(attestation.get("commit"), str)
        or _HEX40.fullmatch(attestation["commit"]) is None
        or not isinstance(attestation.get("tree"), str)
        or _HEX40.fullmatch(attestation["tree"]) is None
        or attestation.get("campaign") != f"b110-pilot-{attestation['commit'][:12]}"
        or not isinstance(attestation.get("manifest_sha256"), str)
        or _HEX64.fullmatch(attestation["manifest_sha256"]) is None
        or not isinstance(attestation.get("snapshot_sha256"), str)
        or _HEX64.fullmatch(attestation["snapshot_sha256"]) is None
        or not isinstance(attestation.get("run_id"), str)
        or _RUN_GATE_RUN_ID.fullmatch(attestation["run_id"]) is None
        or not isinstance(attestation.get("log_path"), str)
        or not Path(attestation["log_path"]).is_absolute()
        or not isinstance(attestation.get("transcript_sha256"), str)
        or _HEX64.fullmatch(attestation["transcript_sha256"]) is None
        or attestation.get("archive_transcript_path") != _ARCHIVED_RUN_GATE_TRANSCRIPT
    ):
        raise ValueError("published pilot run-gate attestation does not bind valid evidence")
    return attestation


def _snapshot_attestation_bytes(attestation: dict[str, Any]) -> bytes:
    return (json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_snapshot_attestation_record(
    assay_fd: int,
) -> tuple[int, os.stat_result, bytes]:
    attestation_fd, attestation_info = _open_regular(
        assay_fd,
        _SNAPSHOT_ATTESTATION,
        label="published pilot run-gate attestation",
        limit=4096,
    )
    try:
        _require_read_only(attestation_info, label="run-gate attestation")
        attestation_raw = _read_fd(
            attestation_fd,
            limit=4096,
            label="published pilot run-gate attestation",
        )
        _parse_snapshot_attestation(attestation_raw)
        return attestation_fd, attestation_info, attestation_raw
    except BaseException:
        os.close(attestation_fd)
        raise


def _read_snapshot_receipt(
    assay_fd: int,
    args: argparse.Namespace,
) -> tuple[int, os.stat_result, bytes]:
    receipt_fd, receipt_info, receipt_raw = _read_snapshot_receipt_record(assay_fd)
    try:
        receipt = _parse_snapshot_receipt(receipt_raw)
        expected = {
            "campaign": args.campaign,
            "commit": args.commit,
            "tree": args.tree,
            "manifest_sha256": args.manifest_sha256,
            "snapshot_sha256": args.snapshot_sha256,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("published pilot snapshot receipt differs from the expected source or digest")
        return receipt_fd, receipt_info, receipt_raw
    except BaseException:
        os.close(receipt_fd)
        raise


def _read_snapshot_receipt_record(
    assay_fd: int,
) -> tuple[int, os.stat_result, bytes]:
    receipt_fd, receipt_info = _open_regular(
        assay_fd,
        _SNAPSHOT_RECEIPT,
        label="published pilot snapshot receipt",
        limit=4096,
    )
    try:
        _require_read_only(receipt_info, label="snapshot receipt")
        receipt_raw = _read_fd(receipt_fd, limit=4096, label="published pilot snapshot receipt")
        _parse_snapshot_receipt(receipt_raw)
        return receipt_fd, receipt_info, receipt_raw
    except BaseException:
        os.close(receipt_fd)
        raise


def _read_named_receipt(
    assay_fd: int,
    name: str,
    *,
    label: str,
) -> tuple[int, os.stat_result, bytes]:
    receipt_fd, receipt_info = _open_regular(
        assay_fd,
        name,
        label=label,
        limit=4096,
    )
    try:
        _require_read_only(receipt_info, label=label)
        receipt_raw = _read_fd(receipt_fd, limit=4096, label=label)
        _parse_snapshot_receipt(receipt_raw)
        return receipt_fd, receipt_info, receipt_raw
    except BaseException:
        os.close(receipt_fd)
        raise


def _snapshot_receipt_bytes(
    args: argparse.Namespace,
    *,
    snapshot_sha256: str,
) -> bytes:
    receipt = {
        "schema": "assay-b110-pilot-evidence-receipt/2",
        "campaign": args.campaign,
        "commit": args.commit,
        "tree": args.tree,
        "manifest_sha256": args.manifest_sha256,
        "snapshot_sha256": snapshot_sha256,
    }
    return (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_snapshot_receipt_named(
    assay_fd: int,
    name: str,
    receipt_raw: bytes,
) -> None:
    receipt_fd = _create_snapshot_file(assay_fd, name)
    try:
        _write_all(receipt_fd, receipt_raw)
        os.fsync(receipt_fd)
        os.fchmod(receipt_fd, 0o400)
        receipt_info = os.fstat(receipt_fd)
        _check_same_path(assay_fd, name, receipt_fd, receipt_info)
    finally:
        os.close(receipt_fd)


def _write_archived_run_gate_transcript(
    directory_fd: int,
    transcript_raw: bytes,
) -> tuple[int, os.stat_result]:
    if len(transcript_raw) > _RUN_GATE_LOG_MAX:
        raise ValueError("run-gate transcript exceeds its archival size limit")
    descriptor = _create_snapshot_file(directory_fd, _ARCHIVED_RUN_GATE_TRANSCRIPT)
    try:
        _write_all(descriptor, transcript_raw)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        written_info = os.fstat(descriptor)
        _check_same_path(
            directory_fd,
            _ARCHIVED_RUN_GATE_TRANSCRIPT,
            descriptor,
            written_info,
        )
    finally:
        os.close(descriptor)

    transcript_fd, transcript_info = _open_regular(
        directory_fd,
        _ARCHIVED_RUN_GATE_TRANSCRIPT,
        label="archived successful run-gate transcript",
        limit=_RUN_GATE_LOG_MAX,
    )
    try:
        _require_read_only(transcript_info, label="archived run-gate transcript")
        if _hash_fd(
            transcript_fd,
            label="archived successful run-gate transcript",
            limit=_RUN_GATE_LOG_MAX,
        ) != hashlib.sha256(transcript_raw).hexdigest():
            raise ValueError("archived run-gate transcript changed while being staged")
        _check_same_path(
            directory_fd,
            _ARCHIVED_RUN_GATE_TRANSCRIPT,
            transcript_fd,
            transcript_info,
        )
        return transcript_fd, transcript_info
    except BaseException:
        os.close(transcript_fd)
        raise


def _write_snapshot_receipt(
    assay_fd: int,
    args: argparse.Namespace,
    *,
    snapshot_sha256: str,
) -> None:
    _write_snapshot_receipt_named(
        assay_fd,
        _SNAPSHOT_RECEIPT,
        _snapshot_receipt_bytes(args, snapshot_sha256=snapshot_sha256),
    )


def _path_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _rename_noreplace(
    source: str,
    destination: str,
    *,
    src_dir_fd: int,
    dst_dir_fd: int,
) -> None:
    """Rename one entry atomically without replacing any existing path."""
    if _RENAMEAT2 is None:
        raise OSError(errno.ENOSYS, "renameat2(RENAME_NOREPLACE) is unavailable")
    result = _RENAMEAT2(
        src_dir_fd,
        os.fsencode(source),
        dst_dir_fd,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), source, None, destination)


def _open_private_incomplete_directory(assay_fd: int) -> int:
    try:
        os.mkdir(_SNAPSHOT_INCOMPLETE, 0o700, dir_fd=assay_fd)
    except FileExistsError:
        pass
    descriptor = os.open(
        _SNAPSHOT_INCOMPLETE,
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=assay_fd,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_mode & 0o300 != 0o300
        ):
            raise ValueError(
                "incomplete pilot evidence directory is not private and writable by the current user"
            )
        _check_same_directory_path(assay_fd, _SNAPSHOT_INCOMPLETE, descriptor, info)
        os.fsync(assay_fd)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_private_unverified_fallback(assay_fd: int) -> tuple[str, int]:
    for attempt in range(16):
        directory_name = (
            _SNAPSHOT_UNVERIFIED_FALLBACK
            if attempt == 0
            else f"{_SNAPSHOT_UNVERIFIED_FALLBACK}-{secrets.token_hex(16)}"
        )
        try:
            os.mkdir(directory_name, 0o700, dir_fd=assay_fd)
        except FileExistsError:
            pass
        try:
            descriptor = os.open(
                directory_name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=assay_fd,
            )
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                continue
            raise
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_mode & 0o300 != 0o300
            ):
                os.close(descriptor)
                continue
            _check_same_directory_path(assay_fd, directory_name, descriptor, info)
            os.fsync(assay_fd)
            return directory_name, descriptor
        except (OSError, ValueError):
            os.close(descriptor)
    raise OSError(errno.EEXIST, "could not establish a private unverified-evidence fallback")


def _open_private_quarantine_root(assay_fd: int) -> tuple[str, int]:
    try:
        return _SNAPSHOT_INCOMPLETE, _open_private_incomplete_directory(assay_fd)
    except (OSError, ValueError) as incomplete_error:
        try:
            return _open_private_unverified_fallback(assay_fd)
        except (OSError, ValueError) as fallback_error:
            raise ValueError(
                "neither the incomplete directory nor a private unverified-evidence "
                f"fallback is available (incomplete: {incomplete_error}; fallback: {fallback_error})"
            ) from fallback_error


def _make_snapshot_read_only(snapshot_fd: int, expected_snapshot: set[str]) -> None:
    state_fd = os.open(
        "b110-pilot-state",
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=snapshot_fd,
    )
    try:
        for relative in expected_snapshot | {_SNAPSHOT_INDEX}:
            if relative.startswith("b110-pilot-state/"):
                parent_fd = state_fd
                name = relative.split("/", 1)[1]
            else:
                parent_fd = snapshot_fd
                name = relative
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_fd,
            )
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError(f"snapshot output {relative!r} is not a single-link regular file")
                os.fchmod(descriptor, 0o400)
            finally:
                os.close(descriptor)
        os.fchmod(state_fd, 0o500)
        os.fchmod(snapshot_fd, 0o500)
    finally:
        os.close(state_fd)


def _open_expected_project(args: argparse.Namespace) -> tuple[Path, int, os.stat_result]:
    project = Path(args.project_root)
    if not project.is_absolute():
        raise ValueError("pilot project root must be an absolute path")
    expected_device = getattr(args, "expected_project_device", None)
    expected_inode = getattr(args, "expected_project_inode", None)
    if type(expected_device) is not int or expected_device < 0 or type(expected_inode) is not int or expected_inode < 1:
        raise ValueError("pre-container project identity is missing or malformed")
    try:
        current = os.stat(project, follow_symlinks=False)
    except OSError as exc:
        _raise_filesystem_error(exc, label="cannot inspect pilot project path without following links")
    if not stat.S_ISDIR(current.st_mode):
        raise ValueError("pilot project path is not a real directory")
    if (current.st_dev, current.st_ino) != (expected_device, expected_inode):
        raise ValueError("pilot project path identity differs from its pre-container identity")
    try:
        descriptor = os.open(
            project,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        _raise_filesystem_error(exc, label="cannot open pilot project path without following links")
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        or (opened.st_dev, opened.st_ino) != (expected_device, expected_inode)
    ):
        os.close(descriptor)
        raise ValueError("pilot project path changed while the host verifier opened it")
    return project, descriptor, opened


def clear_prior_outputs(args: argparse.Namespace) -> tuple[int, int, int]:
    """Unlink only the named prior-attempt files from the pinned .assay inode."""
    outputs_by_lane = {
        "b110-screen": (
            "verdict-b110-screen.json",
            "b110-screen-plan.json",
            "b110-screen-run.log",
        ),
        "b110-pilot": (
            "b110-pilot-plan.json",
            "b110-pilot-candidates.txt",
            "b110-pilot-selection.json",
            "b110-pilot-summary.json",
            "b110-pilot-run.log",
            "r2-manifest-b110-pilot.txt",
            "b110-pilot-attempt.log",
            "b110-pilot-attempt-window.json",
            "b110-pilot-artifacts.sha256",
        ),
    }
    lane = args.clear_prior_outputs
    if lane not in outputs_by_lane:
        raise ValueError("stale-output cleanup requires a supported B110 lane")
    expected_device = args.expected_assay_device
    expected_inode = args.expected_assay_inode
    expected_absent = args.expected_assay_absent
    if expected_absent:
        if expected_device is not None or expected_inode is not None:
            raise ValueError("absent .assay identity cannot include device or inode values")
    elif (
        type(expected_device) is not int
        or expected_device < 0
        or type(expected_inode) is not int
        or expected_inode < 1
    ):
        raise ValueError("pre-container .assay identity is missing or malformed")

    project, project_fd, project_info = _open_expected_project(args)
    assay_fd = -1
    created_name: str | None = None
    try:
        try:
            current = os.stat(".assay", dir_fd=project_fd, follow_symlinks=False)
        except FileNotFoundError:
            if expected_absent:
                created_name = f".assay.b110-init.{secrets.token_hex(16)}"
                os.mkdir(created_name, 0o700, dir_fd=project_fd)
                assay_fd = os.open(
                    created_name,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=project_fd,
                )
                assay_info = os.fstat(assay_fd)
                if (
                    not stat.S_ISDIR(assay_info.st_mode)
                    or assay_info.st_uid != os.geteuid()
                    or assay_info.st_mode & 0o077
                    or assay_info.st_mode & 0o300 != 0o300
                ):
                    raise ValueError("new B110 state directory is not private and writable")
                _rename_noreplace(
                    created_name,
                    ".assay",
                    src_dir_fd=project_fd,
                    dst_dir_fd=project_fd,
                )
                created_name = None
                _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
                _check_same_absolute_directory_path(project, project_fd, project_info)
                os.fsync(project_fd)
                return 0, assay_info.st_dev, assay_info.st_ino
            raise ValueError("B110 state directory disappeared before stale-output cleanup")
        if expected_absent:
            raise ValueError("B110 state directory appeared after its initial absence check")
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (expected_device, expected_inode)
        ):
            raise ValueError("B110 state directory identity changed before stale-output cleanup")
        assay_fd = os.open(
            ".assay",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=project_fd,
        )
        assay_info = os.fstat(assay_fd)
        if (
            (assay_info.st_dev, assay_info.st_ino) != (expected_device, expected_inode)
            or (assay_info.st_dev, assay_info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("B110 state directory changed while opening stale-output cleanup root")
        removed = 0
        for name in outputs_by_lane[lane]:
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            try:
                info = os.stat(name, dir_fd=assay_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(info.st_mode):
                raise ValueError(f"B110 stale output is a directory, not a removable file: {name}")
            os.unlink(name, dir_fd=assay_fd)
            removed += 1
        os.fsync(assay_fd)
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        _check_same_absolute_directory_path(project, project_fd, project_info)
        return removed, assay_info.st_dev, assay_info.st_ino
    finally:
        if created_name is not None:
            try:
                current_temp = os.stat(created_name, dir_fd=project_fd, follow_symlinks=False)
                if assay_fd >= 0:
                    opened_temp = os.fstat(assay_fd)
                    if (
                        stat.S_ISDIR(current_temp.st_mode)
                        and (current_temp.st_dev, current_temp.st_ino)
                        == (opened_temp.st_dev, opened_temp.st_ino)
                    ):
                        os.rmdir(created_name, dir_fd=project_fd)
            except OSError:
                pass
        if assay_fd >= 0:
            os.close(assay_fd)
        os.close(project_fd)


def _verify_snapshot_directory(
    snapshot_fd: int,
    args: argparse.Namespace,
    *,
    snapshot_sha256: str,
    inventory_version: int | None = None,
    require_active_deadlines: bool = True,
) -> tuple[int, int, bytes, bytes]:
    _require_read_only(os.fstat(snapshot_fd), label="snapshot directory")
    index_fd, index_info = _open_regular(
        snapshot_fd, _SNAPSHOT_INDEX, label="pilot snapshot index", limit=4 * 1024 * 1024
    )
    state_fd = -1
    candidate_fd = -1
    opened: dict[str, tuple[int, os.stat_result, str]] = {}
    try:
        _require_read_only(index_info, label="pilot snapshot index")
        index_raw = _read_fd(index_fd, limit=4 * 1024 * 1024, label="pilot snapshot index")
        if hashlib.sha256(index_raw).hexdigest() != snapshot_sha256:
            raise ValueError("published pilot snapshot index differs from its completion receipt")
        candidate_fd, _candidate_info = _open_regular(
            snapshot_fd,
            "b110-pilot-candidates.txt",
            label="snapshot pilot candidate file",
            limit=1024 * 1024,
        )
        opened["b110-pilot-candidates.txt"] = (
            candidate_fd,
            os.fstat(candidate_fd),
            "",
        )
        candidate_ids = _candidate_ids(
            _read_fd(candidate_fd, limit=1024 * 1024, label="snapshot pilot candidate file")
        )
        if inventory_version is None:
            inventory_version = getattr(args, "snapshot_inventory_version", 2)
        if inventory_version not in {1, 2}:
            raise ValueError("pilot snapshot inventory version is unsupported")
        expected = _expected_artifacts(
            candidate_ids,
            args.commit,
            inventory_version=inventory_version,
        )
        expected_snapshot = _snapshot_paths(expected)
        index_entries = _parse_manifest(index_raw, expected_snapshot, label="pilot snapshot index")
        if _directory_names(snapshot_fd, label="pilot evidence snapshot") != _snapshot_top_level(expected_snapshot):
            raise ValueError("pilot evidence snapshot has an unexpected top-level inventory")
        state_fd = os.open(
            "b110-pilot-state",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=snapshot_fd,
        )
        state_info = os.fstat(state_fd)
        _require_read_only(state_info, label="snapshot state directory")
        _check_same_directory_path(snapshot_fd, "b110-pilot-state", state_fd, state_info)
        expected_state_names = {"PILOT-STATE", *(f"{identity}.json" for identity in candidate_ids)}
        if _check_state_inventory(state_fd, candidate_ids, label="snapshot pilot state") != expected_state_names:
            raise ValueError("pilot evidence snapshot has an unexpected state inventory")

        for relative in sorted(expected_snapshot):
            if relative == "b110-pilot-candidates.txt":
                descriptor, info = candidate_fd, os.fstat(candidate_fd)
            else:
                descriptor, info = _snapshot_file_fd(
                    snapshot_fd, state_fd, relative, label=f"snapshot artifact {relative}"
                )
            expected_digest = index_entries[relative]
            opened[relative] = (descriptor, info, expected_digest)
            _require_read_only(info, label=f"snapshot artifact {relative}")
            if _hash_fd(descriptor, label=f"snapshot artifact {relative}") != expected_digest:
                raise ValueError(f"published pilot snapshot artifact differs from its index: {relative}")

        manifest_raw = _read_fd(
            opened[_INNER_MANIFEST][0], limit=4 * 1024 * 1024, label="snapshot inner manifest"
        )
        if hashlib.sha256(manifest_raw).hexdigest() != args.manifest_sha256:
            raise ValueError("snapshot inner manifest differs from the host receipt")
        inner_entries = _parse_manifest(manifest_raw, expected, label="snapshot inner manifest")
        for relative, digest in inner_entries.items():
            snapshot_relative = relative.removeprefix(".assay/")
            if index_entries.get(snapshot_relative) != digest:
                raise ValueError(f"snapshot index disagrees with the inner manifest for {relative}")

        deadline_name = f"campaign-deadline-b110-pilot-{args.commit[:12]}.json"
        deadline_raw = _read_fd(
            opened[deadline_name][0], limit=1024 * 1024, label="snapshot campaign deadline"
        )
        window_raw = _read_fd(
            opened["b110-pilot-attempt-window.json"][0],
            limit=1024 * 1024,
            label="snapshot pilot attempt window",
        )
        campaign_expires_ns, attempt_expires_ns = _verify_deadlines(
            deadline_raw=deadline_raw,
            window_raw=window_raw,
            campaign=args.campaign,
            commit=args.commit,
            tree=args.tree,
            require_active=require_active_deadlines,
        )

        if _hash_fd(index_fd, label="pilot snapshot index") != snapshot_sha256:
            raise ValueError("pilot snapshot index changed during host verification")
        _check_same_path(snapshot_fd, _SNAPSHOT_INDEX, index_fd, index_info, require_read_only=True)
        for relative, (descriptor, info, expected_digest) in opened.items():
            if _hash_fd(descriptor, label=f"snapshot artifact {relative}") != expected_digest:
                raise ValueError(f"pilot evidence snapshot changed during host verification: {relative}")
            if relative == _INNER_MANIFEST:
                _check_same_path(snapshot_fd, relative, descriptor, info, require_read_only=True)
            elif relative.startswith("b110-pilot-state/"):
                _check_same_path(
                    state_fd,
                    relative.split("/", 1)[1],
                    descriptor,
                    info,
                    require_read_only=True,
                )
            else:
                _check_same_path(snapshot_fd, relative, descriptor, info, require_read_only=True)
        if _directory_names(snapshot_fd, label="pilot evidence snapshot") != _snapshot_top_level(expected_snapshot):
            raise ValueError("pilot evidence snapshot inventory changed during host verification")
        if _check_state_inventory(state_fd, candidate_ids, label="snapshot pilot state") != expected_state_names:
            raise ValueError("pilot evidence snapshot state inventory changed during host verification")
        _check_same_directory_path(
            snapshot_fd,
            "b110-pilot-state",
            state_fd,
            state_info,
            require_read_only=True,
        )
        now_ns = time.time_ns()
        if require_active_deadlines:
            if now_ns >= attempt_expires_ns:
                raise ValueError("90-minute pilot attempt expired before host completion")
            if now_ns >= campaign_expires_ns:
                raise ValueError("two-hour campaign deadline expired before host completion")
        return campaign_expires_ns, attempt_expires_ns, deadline_raw, window_raw
    finally:
        for descriptor, _info, _digest in opened.values():
            if descriptor != candidate_fd:
                os.close(descriptor)
        if candidate_fd >= 0:
            os.close(candidate_fd)
        if state_fd >= 0:
            os.close(state_fd)
        os.close(index_fd)


def _verify_snapshot_permissions(snapshot_fd: int, args: argparse.Namespace) -> None:
    """Repeat the complete content, identity, and mode check at archive completion."""
    _verify_snapshot_directory(
        snapshot_fd,
        args,
        snapshot_sha256=args.snapshot_sha256,
        require_active_deadlines=False,
    )


def verify_published_snapshot(
    args: argparse.Namespace,
    *,
    allow_pending: bool = False,
) -> str:
    if _HEX40.fullmatch(args.commit) is None or _HEX40.fullmatch(args.tree) is None:
        raise ValueError("expected source commit and tree must be full lowercase Git IDs")
    if _HEX64.fullmatch(args.manifest_sha256) is None:
        raise ValueError("reported attestation digest must be a lowercase SHA-256 digest")
    if _HEX64.fullmatch(args.snapshot_sha256) is None:
        raise ValueError("snapshot digest must be a lowercase SHA-256 digest")
    if args.campaign != f"b110-pilot-{args.commit[:12]}":
        raise ValueError("pilot campaign name does not match the expected source commit")

    project, project_fd, project_info = _open_expected_project(args)
    assay_fd = -1
    snapshot_fd = -1
    state_fd = -1
    receipt_fd = -1
    pending_fd = -1
    try:
        assay_fd = os.open(
            ".assay",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=project_fd,
        )
        assay_info = os.fstat(assay_fd)
        _check_expected_assay_identity(args, assay_info)
        receipt_fd, receipt_info, receipt_raw = _read_snapshot_receipt(assay_fd, args)
        receipt = _parse_snapshot_receipt(receipt_raw)
        snapshot_inventory_version = _receipt_inventory_version(receipt)
        if _path_exists(assay_fd, _SNAPSHOT_PENDING):
            if not allow_pending:
                raise ValueError("published pilot evidence still has an incomplete-publication marker")
            pending_fd, pending_info, pending_raw = _read_named_receipt(
                assay_fd,
                _SNAPSHOT_PENDING,
                label="pending pilot snapshot receipt",
            )
            if pending_raw != receipt_raw:
                raise ValueError("pending pilot snapshot receipt differs from the published receipt")
            _check_same_path(
                assay_fd,
                _SNAPSHOT_PENDING,
                pending_fd,
                pending_info,
                require_read_only=True,
            )
        elif allow_pending:
            raise ValueError("pending pilot snapshot receipt disappeared before publication verification")
        snapshot_fd = os.open(
            _SNAPSHOT_NAME,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=assay_fd,
        )
        snapshot_info = os.fstat(snapshot_fd)
        campaign_expires_ns, attempt_expires_ns, _deadline_raw, _window_raw = _verify_snapshot_directory(
            snapshot_fd,
            args,
            snapshot_sha256=args.snapshot_sha256,
            inventory_version=snapshot_inventory_version,
        )
        state_fd = os.open(
            "b110-pilot-state",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=snapshot_fd,
        )
        state_info = os.fstat(state_fd)
        _check_same_directory_path(
            snapshot_fd,
            "b110-pilot-state",
            state_fd,
            state_info,
            require_read_only=True,
        )
        _check_same_directory_path(
            assay_fd,
            _SNAPSHOT_NAME,
            snapshot_fd,
            snapshot_info,
            require_read_only=True,
        )
        _check_same_path(
            assay_fd,
            _SNAPSHOT_RECEIPT,
            receipt_fd,
            receipt_info,
            require_read_only=True,
        )
        if pending_fd >= 0:
            _check_same_path(
                assay_fd,
                _SNAPSHOT_PENDING,
                pending_fd,
                os.fstat(pending_fd),
                require_read_only=True,
            )
        if _hash_fd(receipt_fd, label="published pilot snapshot receipt") != hashlib.sha256(receipt_raw).hexdigest():
            raise ValueError("published pilot snapshot receipt changed during host verification")
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        _check_same_absolute_directory_path(project, project_fd, project_info)
        now_ns = time.time_ns()
        if now_ns >= attempt_expires_ns:
            raise ValueError("90-minute pilot attempt expired before host completion")
        if now_ns >= campaign_expires_ns:
            raise ValueError("two-hour campaign deadline expired before host completion")
        _check_same_directory_path(
            snapshot_fd,
            "b110-pilot-state",
            state_fd,
            state_info,
            require_read_only=True,
        )
        _check_same_directory_path(
            assay_fd,
            _SNAPSHOT_NAME,
            snapshot_fd,
            snapshot_info,
            require_read_only=True,
        )
        _check_same_path(
            assay_fd,
            _SNAPSHOT_RECEIPT,
            receipt_fd,
            receipt_info,
            require_read_only=True,
        )
        if pending_fd >= 0:
            _check_same_path(
                assay_fd,
                _SNAPSHOT_PENDING,
                pending_fd,
                os.fstat(pending_fd),
                require_read_only=True,
        )
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        _check_same_absolute_directory_path(project, project_fd, project_info)
        _check_same_directory_path(
            assay_fd,
            _SNAPSHOT_NAME,
            snapshot_fd,
            snapshot_info,
            require_read_only=True,
        )
        # Reopen and recheck every snapshot child at the completion boundary.
        # Earlier receipt, deadline, and root checks leave time for a same-user
        # writer to change only a child mode without changing its bytes or inode.
        (
            final_campaign_expires_ns,
            final_attempt_expires_ns,
            _final_deadline_raw,
            _final_window_raw,
        ) = _verify_snapshot_directory(
            snapshot_fd,
            args,
            snapshot_sha256=args.snapshot_sha256,
            inventory_version=snapshot_inventory_version,
        )
        _check_same_directory_path(
            assay_fd,
            _SNAPSHOT_NAME,
            snapshot_fd,
            snapshot_info,
            require_read_only=True,
        )
        # The visible .assay identity is checked after the child pass so a
        # replacement during that pass cannot detach the verified snapshot.
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        _check_same_absolute_directory_path(project, project_fd, project_info)
        _verify_current_source_identity(project, args.commit, args.tree)
        (
            final_campaign_expires_ns,
            final_attempt_expires_ns,
            _final_deadline_raw,
            _final_window_raw,
        ) = _verify_snapshot_directory(
            snapshot_fd,
            args,
            snapshot_sha256=args.snapshot_sha256,
            inventory_version=snapshot_inventory_version,
        )
        if _hash_fd(
            receipt_fd,
            label="published pilot snapshot receipt",
        ) != hashlib.sha256(receipt_raw).hexdigest():
            raise ValueError("published pilot snapshot receipt changed during final host verification")
        _check_same_directory_path(
            assay_fd,
            _SNAPSHOT_NAME,
            snapshot_fd,
            snapshot_info,
            require_read_only=True,
        )
        _check_same_path(
            assay_fd,
            _SNAPSHOT_RECEIPT,
            receipt_fd,
            receipt_info,
            require_read_only=True,
        )
        if pending_fd >= 0:
            _check_same_path(
                assay_fd,
                _SNAPSHOT_PENDING,
                pending_fd,
                os.fstat(pending_fd),
                require_read_only=True,
            )
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        _check_same_absolute_directory_path(project, project_fd, project_info)
        now_ns = time.time_ns()
        if now_ns >= final_attempt_expires_ns:
            raise ValueError("90-minute pilot attempt expired before host completion")
        if now_ns >= final_campaign_expires_ns:
            raise ValueError("two-hour campaign deadline expired before host completion")
        return args.snapshot_sha256
    finally:
        if pending_fd >= 0:
            os.close(pending_fd)
        if state_fd >= 0:
            os.close(state_fd)
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if receipt_fd >= 0:
            os.close(receipt_fd)
        if assay_fd >= 0:
            os.close(assay_fd)
        os.close(project_fd)


def _git_object(project: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--verify", revision],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        raise HistoryUnavailableError(
            f"cannot verify retained pilot source {revision!r}: {detail}"
        )
    return result.stdout.strip()


def _verify_current_source_identity(project: Path, commit: str, tree: str) -> None:
    current_commit = _git_object(project, "HEAD^{commit}")
    current_tree = _git_object(project, "HEAD^{tree}")
    if current_commit != commit or current_tree != tree:
        raise ValueError("selected worktree source identity changed during final B110 host verification")
    result = subprocess.run(
        [
            "git",
            "-c", "maintenance.auto=false",
            "-c", "maintenance.autoDetach=false",
            "-c", "gc.autoDetach=false",
            "-C", str(project),
            "status", "--porcelain", "--untracked-files=all",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        raise HistoryUnavailableError(f"cannot verify final B110 source cleanliness: {detail}")
    if result.stdout:
        raise ValueError("selected worktree became dirty during final B110 host verification")


def _run_gate_evidence_root(expected_worktree: Path) -> Path:
    root = Path(os.environ.get("RUN_GATE_EVIDENCE_DIR") or "/tmp/run-gate")
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("run-gate evidence directory must be an absolute, normalized path")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_worktree = expected_worktree.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve run-gate evidence/worktree paths: {exc}") from exc
    if resolved_root != root:
        raise ValueError("run-gate evidence directory must not traverse symlinks")
    try:
        resolved_root.relative_to(resolved_worktree)
    except ValueError:
        pass
    else:
        raise ValueError("run-gate evidence directory is inside the judged worktree")
    return resolved_root


class _OpenedRunGateLog:
    __slots__ = (
        "log_fd", "log_info", "log_raw", "root_fd", "root_info",
        "lanes_fd", "lanes_info", "lane_fd", "lane_info",
    )

    def __init__(
        self,
        *,
        log_fd: int,
        log_info: os.stat_result,
        log_raw: bytes,
        root_fd: int,
        root_info: os.stat_result,
        lanes_fd: int,
        lanes_info: os.stat_result,
        lane_fd: int,
        lane_info: os.stat_result,
    ) -> None:
        self.log_fd = log_fd
        self.log_info = log_info
        self.log_raw = log_raw
        self.root_fd = root_fd
        self.root_info = root_info
        self.lanes_fd = lanes_fd
        self.lanes_info = lanes_info
        self.lane_fd = lane_fd
        self.lane_info = lane_info

    def check_path(self, root: Path, run_id: str) -> None:
        _check_same_absolute_directory_path(root, self.root_fd, self.root_info)
        _check_same_directory_path(self.root_fd, "lanes", self.lanes_fd, self.lanes_info)
        _check_same_directory_path(self.lanes_fd, "b110-pilot", self.lane_fd, self.lane_info)
        _check_same_path(self.lane_fd, f"{run_id}.log", self.log_fd, self.log_info)

    def close(self) -> None:
        for descriptor in (self.log_fd, self.lane_fd, self.lanes_fd, self.root_fd):
            os.close(descriptor)


def _open_run_gate_log(root: Path, run_id: str, log_path: str) -> _OpenedRunGateLog:
    if _RUN_GATE_RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run-gate history has a malformed run ID")
    expected_path = root / "lanes" / "b110-pilot" / f"{run_id}.log"
    if log_path != str(expected_path):
        raise ValueError("run-gate history log path is outside the expected pilot evidence directory")
    root_fd = lanes_fd = lane_fd = log_fd = -1
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        lanes_fd = os.open(
            "lanes",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        lane_fd = os.open(
            "b110-pilot",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=lanes_fd,
        )
        root_info = os.fstat(root_fd)
        lanes_info = os.fstat(lanes_fd)
        lane_info = os.fstat(lane_fd)
        for descriptor, label, private in (
            (root_fd, "run-gate evidence directory", False),
            (lanes_fd, "run-gate lane directory", False),
            (lane_fd, "run-gate pilot directory", True),
        ):
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError(f"{label} is not a directory owned by the current user")
            if (private and info.st_mode & 0o077) or (not private and info.st_mode & 0o022):
                requirement = "private" if private else "not writable by other users"
                raise ValueError(f"{label} is not {requirement}")
        log_fd, log_info = _open_regular(
            lane_fd,
            f"{run_id}.log",
            label="successful run-gate pilot transcript",
            limit=_RUN_GATE_LOG_MAX,
        )
        if log_info.st_uid != os.geteuid() or not stat.S_IMODE(log_info.st_mode) & 0o400 or log_info.st_mode & 0o077:
            raise ValueError("successful run-gate pilot transcript is not owner-only")
        log_raw = _read_fd(
            log_fd,
            limit=_RUN_GATE_LOG_MAX,
            label="successful run-gate pilot transcript",
        )
        opened = _OpenedRunGateLog(
            log_fd=log_fd,
            log_info=log_info,
            log_raw=log_raw,
            root_fd=root_fd,
            root_info=root_info,
            lanes_fd=lanes_fd,
            lanes_info=lanes_info,
            lane_fd=lane_fd,
            lane_info=lane_info,
        )
        opened.check_path(root, run_id)
        root_fd = lanes_fd = lane_fd = log_fd = -1
        return opened
    except BaseException:
        if log_fd >= 0:
            os.close(log_fd)
        raise
    finally:
        for descriptor in (lane_fd, lanes_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


def _transcript_marker(raw: bytes, name: bytes, pattern: re.Pattern[str]) -> str | None:
    prefix = name + b"="
    values = [line[len(prefix):] for line in raw.splitlines() if line.startswith(prefix)]
    if len(values) != 1:
        return None
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    return value if pattern.fullmatch(value) is not None else None


def _validate_b110_history_record(
    entry: object,
    *,
    label: str,
    historical: bool,
) -> None:
    if not isinstance(entry, dict):
        raise HistoryUnavailableError(f"run-gate B110 pilot {label} is malformed")
    commit = entry.get("commit")
    if entry.get("lane") != "b110-pilot" or not isinstance(entry.get("worktree"), str) or not entry["worktree"]:
        raise HistoryUnavailableError(f"run-gate B110 pilot {label} is malformed")
    outcome = entry.get("outcome")
    if not isinstance(outcome, str) or outcome not in {
        "pass", "fail", "aborted", "error", "not_run", "budget_exceeded"
    }:
        raise HistoryUnavailableError(f"run-gate B110 pilot {label} has an invalid outcome")
    history_eligible = entry.get("history_eligible")
    if type(history_eligible) is not bool:
        raise HistoryUnavailableError(f"run-gate B110 pilot {label} has malformed eligibility")
    if historical and (outcome not in {"pass", "fail"} or history_eligible is not True):
        raise HistoryUnavailableError(f"run-gate B110 pilot {label} is not an eligible completion")
    commit_is_valid = (
        isinstance(commit, str)
        and (_HEX40.fullmatch(commit) is not None or _HEX64.fullmatch(commit) is not None)
    )
    if history_eligible and not commit_is_valid:
        raise HistoryUnavailableError(f"run-gate B110 pilot {label} has no eligible commit")
    if commit is not None and not commit_is_valid:
        raise HistoryUnavailableError(f"run-gate B110 pilot {label} has a malformed commit")
    if outcome in {"pass", "fail"}:
        exit_code = entry.get("exit_code")
        if type(exit_code) is not int or (outcome == "pass") != (exit_code == 0):
            raise HistoryUnavailableError(
                f"run-gate B110 pilot {label} has an outcome/exit-code mismatch"
            )
    else:
        exit_code = entry.get("exit_code")
        if exit_code is not None and type(exit_code) is not int:
            raise HistoryUnavailableError(
                f"run-gate B110 pilot {label} has a malformed exit code"
            )
        if history_eligible:
            raise HistoryUnavailableError(f"run-gate B110 pilot {label} has impossible eligibility")
    if history_eligible:
        run_id = entry.get("run_id")
        log_path = entry.get("log_path")
        if (
            not isinstance(run_id, str)
            or _RUN_GATE_RUN_ID.fullmatch(run_id) is None
            or not isinstance(log_path, str)
            or not Path(log_path).is_absolute()
        ):
            raise HistoryUnavailableError(
                f"successful run-gate B110 pilot {label} has no transcript binding"
            )


def _preflight_history_store(project: Path) -> dict[str, Any]:
    """Reject run-gate's documented empty-store fallback for corrupt history."""
    history_dir_fd = history_fd = -1
    try:
        history_dir_fd = os.open(
            project / ".run-gate",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        directory_info = os.fstat(history_dir_fd)
        if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != os.geteuid():
            raise HistoryUnavailableError("run-gate history directory is not owned by the current user")
        history_fd, history_info = _open_regular(
            history_dir_fd,
            "history.json",
            label="run-gate history store",
            limit=_RUN_GATE_HISTORY_MAX,
        )
        if history_info.st_uid != os.geteuid() or history_info.st_mode & 0o022:
            raise HistoryUnavailableError("run-gate history store is not safely owned by the current user")
        raw = _read_fd(
            history_fd,
            limit=_RUN_GATE_HISTORY_MAX,
            label="run-gate history store",
        )
        _check_same_path(history_dir_fd, "history.json", history_fd, history_info)
        document = _parse_json(raw, label="run-gate history store")
        if document.get("schema") != 2 or not isinstance(document.get("lanes"), dict):
            raise HistoryUnavailableError("run-gate history store has an unsupported or malformed schema")
        pilot_lanes = document["lanes"]
        if "b110-pilot" in pilot_lanes:
            pilot_slot = pilot_lanes["b110-pilot"]
            if not isinstance(pilot_slot, dict):
                raise HistoryUnavailableError("run-gate B110 pilot history lane is malformed")
            history = pilot_slot.get("history")
            if not isinstance(history, list):
                raise HistoryUnavailableError("run-gate B110 pilot history entries are malformed")
            latest = pilot_slot.get("latest")
            if not isinstance(latest, dict):
                raise HistoryUnavailableError("run-gate B110 pilot history latest entry is malformed")
            for index, entry in enumerate(history):
                _validate_b110_history_record(
                    entry,
                    label=f"history entry {index}",
                    historical=True,
                )
            _validate_b110_history_record(latest, label="latest entry", historical=False)
        return document
    except HistoryUnavailableError:
        raise
    except (OSError, ValueError) as exc:
        raise HistoryUnavailableError(f"run-gate history store is unavailable: {exc}") from exc
    finally:
        if history_fd >= 0:
            os.close(history_fd)
        if history_dir_fd >= 0:
            os.close(history_dir_fd)


def _has_successful_run_gate_attestation(
    project: Path,
    receipt: dict[str, Any],
    expected_worktree: Path,
) -> tuple[dict[str, Any], bytes]:
    """Match the retained receipt to a successful registered lane transcript.

    The validated in-memory store snapshot identifies candidate logs. Reading
    the store again through ``run-gate history`` would allow an atomic writer
    to replace it between preflight and query, turning malformed new bytes
    into the CLI's documented empty-store answer. A transcript must contain
    both the provisional digest and the host's post-verification digest
    before it can validate a retained receipt.
    """
    store = _preflight_history_store(project)
    lane = store["lanes"].get("b110-pilot")
    if lane is None:
        raise NoSuccessfulAttestationError(
            "readable run-gate history has no successful retained B110 pilot transcript"
        )
    entries = [*lane["history"], lane["latest"]]
    candidates: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if (
            entry.get("lane") != "b110-pilot"
            or entry.get("worktree") != str(expected_worktree)
        ):
            raise HistoryUnavailableError("run-gate pilot history entry does not bind the retained worktree")
        if (
            entry.get("commit") != receipt["commit"]
            or entry.get("outcome") != "pass"
            or type(entry.get("exit_code")) is not int
            or entry.get("exit_code") != 0
            or entry.get("history_eligible") is not True
        ):
            continue
        candidates[entry["run_id"]] = entry
    if not candidates:
        raise NoSuccessfulAttestationError(
            "readable run-gate history has no successful retained B110 pilot transcript"
        )
    try:
        evidence_root = _run_gate_evidence_root(expected_worktree)
    except (OSError, ValueError) as exc:
        raise HistoryUnavailableError(f"run-gate pilot transcript directory is unavailable: {exc}") from exc

    verified_digests: set[str] = set()
    unavailable: list[str] = []
    for entry in candidates.values():
        opened_log: _OpenedRunGateLog | None = None
        try:
            opened_log = _open_run_gate_log(
                evidence_root,
                entry["run_id"],
                entry["log_path"],
            )
            log_raw = opened_log.log_raw
            source_commit = _transcript_marker(log_raw, b"B105_SOURCE_COMMIT", _HEX40)
            source_tree = _transcript_marker(log_raw, b"B105_SOURCE_TREE", _HEX40)
            snapshot_digest = _transcript_marker(log_raw, b"B110_PILOT_SNAPSHOT_SHA256", _HEX64)
            host_verified_digest = _transcript_marker(
                log_raw,
                b"B110_PILOT_HOST_VERIFIED_SHA256",
                _HEX64,
            )
            if source_commit != receipt["commit"] or source_tree != receipt["tree"]:
                unavailable.append(f"run {entry['run_id']} transcript does not bind its history source")
                continue
            if snapshot_digest is None or host_verified_digest is None or snapshot_digest != host_verified_digest:
                unavailable.append(f"run {entry['run_id']} transcript lacks a final host-verification marker")
                continue
            verified_digests.add(snapshot_digest)
            if snapshot_digest == receipt["snapshot_sha256"]:
                transcript_sha256 = hashlib.sha256(log_raw).hexdigest()
                if _hash_fd(
                    opened_log.log_fd,
                    label="successful run-gate pilot transcript",
                    limit=_RUN_GATE_LOG_MAX,
                ) != transcript_sha256:
                    unavailable.append(f"run {entry['run_id']} transcript changed during host verification")
                    continue
                opened_log.check_path(evidence_root, entry["run_id"])
                return (
                    {
                        "schema": "assay-b110-pilot-run-gate-attestation/2",
                        "campaign": receipt["campaign"],
                        "commit": receipt["commit"],
                        "tree": receipt["tree"],
                        "manifest_sha256": receipt["manifest_sha256"],
                        "snapshot_sha256": receipt["snapshot_sha256"],
                        "run_id": entry["run_id"],
                        "log_path": entry["log_path"],
                        "transcript_sha256": transcript_sha256,
                        "archive_transcript_path": _ARCHIVED_RUN_GATE_TRANSCRIPT,
                    },
                    log_raw,
                )
        except (OSError, ValueError) as exc:
            unavailable.append(f"run {entry['run_id']} transcript unavailable: {exc}")
        finally:
            if opened_log is not None:
                opened_log.close()
    if unavailable:
        detail = re.sub(r"[\r\n]+", " ", unavailable[0])[:240]
        raise HistoryUnavailableError(detail)
    if verified_digests:
        raise PriorEvidenceMismatch("valid successful run-gate transcript binds a different prior snapshot digest")
    raise HistoryUnavailableError("no usable successful run-gate transcript is available")


def _verify_persisted_run_gate_attestation(
    receipt: dict[str, Any],
    attestation: dict[str, Any],
    expected_worktree: Path,
) -> bytes:
    for key in (
        "campaign", "commit", "tree", "manifest_sha256", "snapshot_sha256",
    ):
        if attestation[key] != receipt[key]:
            raise ValueError("published pilot run-gate attestation differs from its snapshot receipt")
    try:
        evidence_root = _run_gate_evidence_root(expected_worktree)
        opened_log = _open_run_gate_log(
            evidence_root,
            attestation["run_id"],
            attestation["log_path"],
        )
    except (OSError, ValueError) as exc:
        raise HistoryUnavailableError(
            f"durable successful run-gate transcript is unavailable: {exc}"
        ) from exc
    try:
        if hashlib.sha256(opened_log.log_raw).hexdigest() != attestation["transcript_sha256"]:
            raise HistoryUnavailableError("durable successful run-gate transcript changed")
        source_commit = _transcript_marker(opened_log.log_raw, b"B105_SOURCE_COMMIT", _HEX40)
        source_tree = _transcript_marker(opened_log.log_raw, b"B105_SOURCE_TREE", _HEX40)
        snapshot_digest = _transcript_marker(opened_log.log_raw, b"B110_PILOT_SNAPSHOT_SHA256", _HEX64)
        host_verified_digest = _transcript_marker(
            opened_log.log_raw,
            b"B110_PILOT_HOST_VERIFIED_SHA256",
            _HEX64,
        )
        if (
            source_commit != receipt["commit"]
            or source_tree != receipt["tree"]
            or snapshot_digest != receipt["snapshot_sha256"]
            or host_verified_digest != receipt["snapshot_sha256"]
        ):
            raise ValueError("durable successful run-gate transcript does not bind the published snapshot")
        if _hash_fd(
            opened_log.log_fd,
            label="durable successful run-gate transcript",
            limit=_RUN_GATE_LOG_MAX,
        ) != attestation["transcript_sha256"]:
            raise HistoryUnavailableError("durable successful run-gate transcript changed during host verification")
        # Recheck the complete path after digest and marker validation. The
        # open descriptor pins bytes; these parent descriptors prove the
        # attested absolute path still names that same inode.
        opened_log.check_path(evidence_root, attestation["run_id"])
        return opened_log.log_raw
    finally:
        opened_log.close()


def _verify_archived_run_gate_transcript(
    archive_stage_fd: int,
    receipt: dict[str, Any],
    attestation: dict[str, Any],
    transcript_fd: int,
    transcript_info: os.stat_result,
) -> tuple[int, os.stat_result]:
    _require_read_only(transcript_info, label="archived run-gate transcript")
    transcript_raw = _read_fd(
        transcript_fd,
        limit=_RUN_GATE_LOG_MAX,
        label="archived successful run-gate transcript",
    )
    if hashlib.sha256(transcript_raw).hexdigest() != attestation["transcript_sha256"]:
        raise ValueError("archived successful run-gate transcript digest differs from its attestation")
    if (
        _transcript_marker(transcript_raw, b"B105_SOURCE_COMMIT", _HEX40) != receipt["commit"]
        or _transcript_marker(transcript_raw, b"B105_SOURCE_TREE", _HEX40) != receipt["tree"]
        or _transcript_marker(transcript_raw, b"B110_PILOT_SNAPSHOT_SHA256", _HEX64)
        != receipt["snapshot_sha256"]
        or _transcript_marker(transcript_raw, b"B110_PILOT_HOST_VERIFIED_SHA256", _HEX64)
        != receipt["snapshot_sha256"]
    ):
        raise ValueError("archived successful run-gate transcript does not bind its snapshot receipt")
    if _hash_fd(
        transcript_fd,
        label="archived successful run-gate transcript",
        limit=_RUN_GATE_LOG_MAX,
    ) != attestation["transcript_sha256"]:
        raise ValueError("archived successful run-gate transcript changed during verification")
    _check_same_path(
        archive_stage_fd,
        _ARCHIVED_RUN_GATE_TRANSCRIPT,
        transcript_fd,
        transcript_info,
        require_read_only=True,
    )
    return transcript_fd, transcript_info


def _withdraw_unverified_archive_entry(
    archive_fd: int,
    incomplete_fd: int,
    archive_name: str,
    assay_fd: int,
) -> str | None:
    """Move a path-published entry out of the visible archive after an inode mismatch."""
    collision_errnos = {errno.EEXIST, errno.ENOTEMPTY, errno.EISDIR, errno.ENOTDIR}
    destination_fd = incomplete_fd
    owns_destination_fd = False
    fallback_name: str | None = None
    destination_name = _SNAPSHOT_INCOMPLETE
    destination_info = os.fstat(destination_fd)
    withdrawn_names: list[str] = []
    try:
        for _attempt in range(16):
            try:
                _check_same_directory_path(
                    assay_fd,
                    destination_name,
                    destination_fd,
                    destination_info,
                    require_private_writable=True,
                )
            except (OSError, ValueError):
                if owns_destination_fd:
                    os.close(destination_fd)
                    owns_destination_fd = False
                destination_name, destination_fd = _open_private_quarantine_root(assay_fd)
                owns_destination_fd = True
                destination_info = os.fstat(destination_fd)
                fallback_name = (
                    destination_name
                    if destination_name != _SNAPSHOT_INCOMPLETE
                    else None
                )
                continue
            quarantine_name = f"unverified-archive-entry-{secrets.token_hex(16)}"
            try:
                _rename_noreplace(
                    archive_name,
                    quarantine_name,
                    src_dir_fd=archive_fd,
                    dst_dir_fd=destination_fd,
                )
            except FileNotFoundError:
                # ENOENT can name either side of renameat2. Only treat it as an
                # already-withdrawn source after proving the visible archive
                # path is gone. If the source remains, reopen the destination
                # directory by its trusted .assay path and retry there.
                if not _path_exists(archive_fd, archive_name):
                    if withdrawn_names:
                        _check_same_directory_path(
                            assay_fd,
                            destination_name,
                            destination_fd,
                            destination_info,
                            require_private_writable=True,
                        )
                    return "; ".join(withdrawn_names) if withdrawn_names else None
                if owns_destination_fd:
                    os.close(destination_fd)
                    owns_destination_fd = False
                destination_name, destination_fd = _open_private_quarantine_root(assay_fd)
                owns_destination_fd = True
                destination_info = os.fstat(destination_fd)
                fallback_name = (
                    destination_name
                    if destination_name != _SNAPSHOT_INCOMPLETE
                    else None
                )
                continue
            except OSError as exc:
                if exc.errno in collision_errnos:
                    continue
                raise
            os.fsync(archive_fd)
            os.fsync(destination_fd)
            # A directory descriptor pins the inode, not its advertised path.
            # Do not return a quarantine location after a concurrent rename
            # has moved that directory elsewhere in .assay.
            _check_same_directory_path(
                assay_fd,
                destination_name,
                destination_fd,
                destination_info,
                require_private_writable=True,
            )
            moved_path = (
                f"{fallback_name}/{quarantine_name}"
                if fallback_name is not None
                else quarantine_name
            )
            withdrawn_names.append(moved_path)
            if not _path_exists(archive_fd, archive_name):
                _check_same_directory_path(
                    assay_fd,
                    destination_name,
                    destination_fd,
                    destination_info,
                    require_private_writable=True,
                )
                return "; ".join(withdrawn_names)
        raise FileExistsError(errno.EEXIST, "could not allocate a unique incomplete archive name")
    finally:
        if owns_destination_fd:
            os.close(destination_fd)


def _prior_evidence_names(assay_fd: int) -> list[str]:
    try:
        names = set(os.listdir(assay_fd))
    except OSError as exc:
        _raise_filesystem_error(exc, label="cannot inspect prior pilot evidence entries")
    selected = {
        name
        for name in (
            _SNAPSHOT_NAME,
            _SNAPSHOT_RECEIPT,
            _SNAPSHOT_ATTESTATION,
            _SNAPSHOT_PENDING,
        )
        if name in names
    }
    selected.update(
        name for name in names if name.startswith(f".{_SNAPSHOT_NAME}.stage.")
    )
    return sorted(selected)


def _quarantine_prior_evidence(
    assay_fd: int,
    *,
    project_fd: int,
    reason: str,
) -> str:
    """Preserve untrusted or incomplete prior publication without validating it."""
    assay_info = os.fstat(assay_fd)
    names = _prior_evidence_names(assay_fd)
    if not names:
        raise ValueError("prior pilot evidence disappeared before quarantine")
    quarantine_root_name, incomplete_fd = _open_private_quarantine_root(assay_fd)
    try:
        incomplete_info = os.fstat(incomplete_fd)
        if (
            incomplete_info.st_uid != os.geteuid()
            or incomplete_info.st_mode & 0o077
        ):
            raise ValueError("incomplete pilot evidence directory is not private to the current user")
        quarantine_name = f"{time.time_ns()}-{secrets.token_hex(6)}"
        os.mkdir(quarantine_name, 0o700, dir_fd=incomplete_fd)
        quarantine_fd = os.open(
            quarantine_name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=incomplete_fd,
        )
        try:
            quarantine_info = os.fstat(quarantine_fd)
            if quarantine_info.st_uid != os.geteuid() or quarantine_info.st_mode & 0o077:
                raise ValueError("new incomplete pilot evidence directory is not private")
            for name in names:
                # renameat moves a symlink itself instead of following it;
                # the quarantined entry is never parsed or trusted.
                try:
                    entry_info = os.stat(name, dir_fd=assay_fd, follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise ValueError(f"prior pilot evidence changed before quarantine: {name}") from exc
                entry_fd = -1
                original_mode: int | None = None
                mode_changed = False
                try:
                    if stat.S_ISDIR(entry_info.st_mode):
                        entry_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=assay_fd,
                        )
                        opened_info = os.fstat(entry_fd)
                        if (opened_info.st_dev, opened_info.st_ino) != (entry_info.st_dev, entry_info.st_ino):
                            raise ValueError(f"prior pilot evidence changed before quarantine: {name}")
                        original_mode = stat.S_IMODE(opened_info.st_mode)
                        os.fchmod(entry_fd, original_mode | stat.S_IWUSR)
                        mode_changed = True
                    _rename_noreplace(
                        name,
                        name,
                        src_dir_fd=assay_fd,
                        dst_dir_fd=quarantine_fd,
                    )
                    if entry_fd >= 0:
                        _check_same_directory_path(quarantine_fd, name, entry_fd, entry_info)
                    else:
                        moved_info = os.stat(name, dir_fd=quarantine_fd, follow_symlinks=False)
                        if (
                            (moved_info.st_dev, moved_info.st_ino, stat.S_IFMT(moved_info.st_mode))
                            != (entry_info.st_dev, entry_info.st_ino, stat.S_IFMT(entry_info.st_mode))
                        ):
                            raise ValueError(f"prior pilot evidence changed during quarantine: {name}")
                finally:
                    if entry_fd >= 0 and mode_changed and original_mode is not None:
                        try:
                            os.fchmod(entry_fd, original_mode)
                        finally:
                            os.close(entry_fd)
                    elif entry_fd >= 0:
                        os.close(entry_fd)
            os.fsync(quarantine_fd)
            # The open descriptors keep bytes reachable if a same-user
            # process renames the quarantine tree, but the returned path
            # would then be false. Check the child while its descriptor is
            # still open; check the root again immediately before reporting.
            _check_same_directory_path(
                incomplete_fd,
                quarantine_name,
                quarantine_fd,
                quarantine_info,
                require_private_writable=True,
            )
        finally:
            os.close(quarantine_fd)
        os.fsync(incomplete_fd)
        os.fsync(assay_fd)
        _check_same_directory_path(
            assay_fd,
            quarantine_root_name,
            incomplete_fd,
            incomplete_info,
            require_private_writable=True,
        )
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        print(f"B110_PILOT_PRIOR_QUARANTINE_REASON={reason}", file=sys.stderr)
        return f"{quarantine_root_name}/{quarantine_name}"
    finally:
        os.close(incomplete_fd)


def archive_prior_snapshot(args: argparse.Namespace) -> tuple[str, str | None]:
    """Archive only snapshots bound to a successful run-gate transcript."""
    expected_commit = args.expected_commit
    expected_tree = args.expected_tree
    if _HEX40.fullmatch(expected_commit) is None or _HEX40.fullmatch(expected_tree) is None:
        raise ValueError("current source commit and tree must be full lowercase Git IDs")

    project, project_fd, project_info = _open_expected_project(args)
    assay_fd = -1
    snapshot_fd = -1
    receipt_fd = -1
    attestation_fd = -1
    archive_transcript_fd = -1
    archive_fd = -1
    incomplete_fd = -1
    archive_stage_fd = -1
    try:
        current_commit = _git_object(project, "HEAD^{commit}")
        current_tree = _git_object(project, "HEAD^{tree}")
        if current_commit != expected_commit or current_tree != expected_tree:
            raise ValueError("current source identity changed before prior snapshot archival")

        assay_fd = os.open(
            ".assay",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=project_fd,
        )
        assay_info = os.fstat(assay_fd)
        _check_expected_assay_identity(args, assay_info)
        prior_names = _prior_evidence_names(assay_fd)
        if not prior_names:
            return "none", None
        try:
            if _SNAPSHOT_PENDING in prior_names or any(
                name.startswith(f".{_SNAPSHOT_NAME}.stage.") for name in prior_names
            ):
                raise ValueError("prior pilot publication is incomplete")
            if _SNAPSHOT_NAME not in prior_names or _SNAPSHOT_RECEIPT not in prior_names:
                raise ValueError("prior pilot snapshot and receipt are not both present")
            receipt_fd, receipt_info, receipt_raw = _read_snapshot_receipt_record(assay_fd)
            receipt = _parse_snapshot_receipt(receipt_raw)
            retained_commit = receipt["commit"]
            retained_tree = receipt["tree"]
            if _git_object(project, f"{retained_commit}^{{commit}}") != retained_commit:
                raise ValueError("prior pilot receipt commit does not resolve to its retained commit")
            if _git_object(project, f"{retained_commit}^{{tree}}") != retained_tree:
                raise ValueError("prior pilot receipt tree differs from its retained source commit")
            if _SNAPSHOT_ATTESTATION in prior_names:
                attestation_fd, attestation_info, attestation_raw = _read_snapshot_attestation_record(
                    assay_fd
                )
                attestation = _parse_snapshot_attestation(attestation_raw)
                transcript_raw = _verify_persisted_run_gate_attestation(
                    receipt,
                    attestation,
                    project.parent,
                )
            else:
                attestation_info = None
                attestation_raw = None
                attestation, transcript_raw = _has_successful_run_gate_attestation(
                    project,
                    receipt,
                    project.parent,
                )

            retained_args = argparse.Namespace(
                project_root=args.project_root,
                expected_project_device=args.expected_project_device,
                expected_project_inode=args.expected_project_inode,
                campaign=receipt["campaign"],
                commit=retained_commit,
                tree=retained_tree,
                manifest_sha256=receipt["manifest_sha256"],
                snapshot_sha256=receipt["snapshot_sha256"],
                snapshot_inventory_version=_receipt_inventory_version(receipt),
            )
            snapshot_fd = os.open(
                _SNAPSHOT_NAME,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=assay_fd,
            )
            snapshot_info = os.fstat(snapshot_fd)
            _verify_snapshot_directory(
                snapshot_fd,
                retained_args,
                snapshot_sha256=receipt["snapshot_sha256"],
                require_active_deadlines=False,
            )
            _check_same_path(
                assay_fd,
                _SNAPSHOT_RECEIPT,
                receipt_fd,
                receipt_info,
                require_read_only=True,
            )
            _check_same_directory_path(
                assay_fd,
                _SNAPSHOT_NAME,
                snapshot_fd,
                snapshot_info,
                require_read_only=True,
            )
            if _hash_fd(receipt_fd, label="published pilot snapshot receipt") != hashlib.sha256(receipt_raw).hexdigest():
                raise ValueError("published pilot snapshot receipt changed during archival verification")
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            _check_same_absolute_directory_path(project, project_fd, project_info)
            if attestation_fd >= 0:
                _check_same_path(
                    assay_fd,
                    _SNAPSHOT_ATTESTATION,
                    attestation_fd,
                    attestation_info,
                    require_read_only=True,
                )
                if _hash_fd(
                    attestation_fd,
                    label="published pilot run-gate attestation",
                ) != hashlib.sha256(attestation_raw).hexdigest():
                    raise ValueError("published pilot run-gate attestation changed during archival verification")
            else:
                attestation_raw = _snapshot_attestation_bytes(attestation)
                _write_snapshot_receipt_named(assay_fd, _SNAPSHOT_ATTESTATION, attestation_raw)
                attestation_fd, attestation_info, readback_raw = _read_snapshot_attestation_record(
                    assay_fd
                )
                if readback_raw != attestation_raw:
                    raise ValueError("new run-gate attestation changed during publication")
                _check_same_path(
                    assay_fd,
                    _SNAPSHOT_ATTESTATION,
                    attestation_fd,
                    attestation_info,
                    require_read_only=True,
                )
                verified_transcript_raw = _verify_persisted_run_gate_attestation(
                    receipt,
                    _parse_snapshot_attestation(readback_raw),
                    project.parent,
                )
                if verified_transcript_raw != transcript_raw:
                    raise HistoryUnavailableError("run-gate transcript changed while saving its attestation")
        except NoSuccessfulAttestationError as exc:
            detail = re.sub(r"[\r\n]+", " ", str(exc))[:240]
            print(f"b110_pilot_host_check: quarantining prior evidence: {detail}", file=sys.stderr)
            quarantine_name = _quarantine_prior_evidence(
                assay_fd,
                project_fd=project_fd,
                reason="no-successful-run-gate-attestation",
            )
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            return "quarantined", quarantine_name
        except HistoryUnavailableError:
            raise
        except (OSError, ValueError) as exc:
            if isinstance(exc, OSError) and exc.errno not in _STRUCTURAL_PATH_ERRNOS:
                raise HistoryUnavailableError(
                    f"prior pilot evidence could not be read or inspected: {exc}"
                ) from exc
            if snapshot_fd >= 0:
                os.close(snapshot_fd)
                snapshot_fd = -1
            if receipt_fd >= 0:
                os.close(receipt_fd)
                receipt_fd = -1
            detail = re.sub(r"[\r\n]+", " ", str(exc))[:240]
            print(f"b110_pilot_host_check: quarantining prior evidence: {detail}", file=sys.stderr)
            quarantine_name = _quarantine_prior_evidence(
                assay_fd,
                project_fd=project_fd,
                reason="untrusted-or-incomplete-prior-publication",
            )
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            return "quarantined", quarantine_name

        try:
            os.mkdir("b110-pilot-evidence-archive", 0o700, dir_fd=assay_fd)
        except FileExistsError:
            pass
        archive_fd = os.open(
            "b110-pilot-evidence-archive",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=assay_fd,
        )
        archive_info = os.fstat(archive_fd)
        if (
            not stat.S_ISDIR(archive_info.st_mode)
            or archive_info.st_uid != os.geteuid()
            or archive_info.st_mode & 0o077
            or archive_info.st_mode & 0o300 != 0o300
        ):
            raise ValueError("pilot evidence archive directory is not private and writable by the current user")
        _check_same_directory_path(
            assay_fd,
            "b110-pilot-evidence-archive",
            archive_fd,
            archive_info,
            require_private_writable=True,
        )
        try:
            os.mkdir(_SNAPSHOT_INCOMPLETE, 0o700, dir_fd=assay_fd)
        except FileExistsError:
            pass
        incomplete_fd = os.open(
            _SNAPSHOT_INCOMPLETE,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=assay_fd,
        )
        incomplete_info = os.fstat(incomplete_fd)
        if (
            not stat.S_ISDIR(incomplete_info.st_mode)
            or incomplete_info.st_uid != os.geteuid()
            or incomplete_info.st_mode & 0o077
            or incomplete_info.st_mode & 0o300 != 0o300
        ):
            raise ValueError("incomplete pilot evidence directory is not private and writable by the current user")
        _check_same_directory_path(
            assay_fd,
            _SNAPSHOT_INCOMPLETE,
            incomplete_fd,
            incomplete_info,
            require_private_writable=True,
        )

        nonce = secrets.token_hex(8)
        archive_name = f"{time.time_ns()}-{nonce}-{receipt['snapshot_sha256']}"
        archive_stage_name = f"archive-stage-{archive_name}"
        os.mkdir(archive_stage_name, 0o700, dir_fd=incomplete_fd)
        archive_stage_fd = os.open(
            archive_stage_name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=incomplete_fd,
        )
        archive_stage_info = os.fstat(archive_stage_fd)
        if (
            archive_stage_info.st_uid != os.geteuid()
            or archive_stage_info.st_mode & 0o077
            or archive_stage_info.st_mode & 0o300 != 0o300
        ):
            raise ValueError("pilot evidence archive staging directory is not private")

        _check_same_path(
            assay_fd,
            _SNAPSHOT_RECEIPT,
            receipt_fd,
            receipt_info,
            require_read_only=True,
        )
        _check_same_directory_path(
            assay_fd,
            _SNAPSHOT_NAME,
            snapshot_fd,
            snapshot_info,
            require_read_only=True,
        )
        _check_same_path(
            assay_fd,
            _SNAPSHOT_ATTESTATION,
            attestation_fd,
            attestation_info,
            require_read_only=True,
        )
        original_snapshot_mode = stat.S_IMODE(snapshot_info.st_mode)
        os.fchmod(snapshot_fd, original_snapshot_mode | stat.S_IWUSR)
        try:
            os.rename(
                _SNAPSHOT_NAME,
                _SNAPSHOT_NAME,
                src_dir_fd=assay_fd,
                dst_dir_fd=archive_stage_fd,
            )
        finally:
            os.fchmod(snapshot_fd, original_snapshot_mode)
        os.rename(
            _SNAPSHOT_RECEIPT,
            _SNAPSHOT_RECEIPT,
            src_dir_fd=assay_fd,
            dst_dir_fd=archive_stage_fd,
        )
        os.rename(
            _SNAPSHOT_ATTESTATION,
            _SNAPSHOT_ATTESTATION,
            src_dir_fd=assay_fd,
            dst_dir_fd=archive_stage_fd,
        )
        archive_transcript_fd, archive_transcript_info = _write_archived_run_gate_transcript(
            archive_stage_fd,
            transcript_raw,
        )
        os.fsync(assay_fd)
        os.fsync(archive_stage_fd)
        os.fsync(incomplete_fd)

        _check_same_directory_path(
            archive_stage_fd,
            _SNAPSHOT_NAME,
            snapshot_fd,
            snapshot_info,
            require_read_only=True,
        )
        _check_same_path(
            archive_stage_fd,
            _SNAPSHOT_RECEIPT,
            receipt_fd,
            receipt_info,
            require_read_only=True,
        )
        _check_same_path(
            archive_stage_fd,
            _SNAPSHOT_ATTESTATION,
            attestation_fd,
            attestation_info,
            require_read_only=True,
        )
        _verify_snapshot_directory(
            snapshot_fd,
            retained_args,
            snapshot_sha256=receipt["snapshot_sha256"],
            require_active_deadlines=False,
        )
        if _hash_fd(receipt_fd, label="archived pilot snapshot receipt") != hashlib.sha256(receipt_raw).hexdigest():
            raise ValueError("archived pilot snapshot receipt changed during archival")
        if _hash_fd(
            attestation_fd,
            label="archived pilot run-gate attestation",
        ) != hashlib.sha256(attestation_raw).hexdigest():
            raise ValueError("archived pilot run-gate attestation changed during archival")
        _verify_archived_run_gate_transcript(
            archive_stage_fd,
            receipt,
            _parse_snapshot_attestation(attestation_raw),
            archive_transcript_fd,
            archive_transcript_info,
        )
        _check_same_directory_path(
            incomplete_fd,
            archive_stage_name,
            archive_stage_fd,
            archive_stage_info,
            require_private_writable=True,
        )
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        _check_same_absolute_directory_path(project, project_fd, project_info)
        # Revalidate the staged, self-contained transcript immediately before
        # the archive bundle's one-rename publication point. The mutable source
        # path may change after the verified bytes have been copied here.
        _verify_archived_run_gate_transcript(
            archive_stage_fd,
            receipt,
            _parse_snapshot_attestation(attestation_raw),
            archive_transcript_fd,
            archive_transcript_info,
        )

        # One same-filesystem rename is the archive commit point: the validated
        # snapshot, receipt, and durable run-gate success binding become visible
        # together or stay in the incomplete area for inspection and retry.
        _rename_noreplace(
            archive_stage_name,
            archive_name,
            src_dir_fd=incomplete_fd,
            dst_dir_fd=archive_fd,
        )
        try:
            os.fsync(incomplete_fd)
            os.fsync(archive_fd)
            _check_same_directory_path(
                archive_stage_fd,
                _SNAPSHOT_NAME,
                snapshot_fd,
                snapshot_info,
            )
            _verify_snapshot_directory(
                snapshot_fd,
                retained_args,
                snapshot_sha256=receipt["snapshot_sha256"],
                require_active_deadlines=False,
            )
            if _hash_fd(
                receipt_fd,
                label="published pilot snapshot receipt",
            ) != hashlib.sha256(receipt_raw).hexdigest():
                raise ValueError("published pilot snapshot receipt changed at archive publication")
            _check_same_path(
                archive_stage_fd,
                _SNAPSHOT_RECEIPT,
                receipt_fd,
                receipt_info,
            )
            if _hash_fd(
                attestation_fd,
                label="published pilot run-gate attestation",
            ) != hashlib.sha256(attestation_raw).hexdigest():
                raise ValueError("published pilot run-gate attestation changed at archive publication")
            _check_same_path(
                archive_stage_fd,
                _SNAPSHOT_ATTESTATION,
                attestation_fd,
                attestation_info,
            )
            _verify_archived_run_gate_transcript(
                archive_stage_fd,
                receipt,
                _parse_snapshot_attestation(attestation_raw),
                archive_transcript_fd,
                archive_transcript_info,
            )
            _check_same_directory_path(
                assay_fd,
                "b110-pilot-evidence-archive",
                archive_fd,
                archive_info,
            )
            _check_same_absolute_directory_path(project, project_fd, project_info)
            _check_same_directory_path(
                archive_fd,
                archive_name,
                archive_stage_fd,
                archive_stage_info,
            )
            if _hash_fd(
                receipt_fd,
                label="published pilot snapshot receipt",
            ) != hashlib.sha256(receipt_raw).hexdigest():
                raise ValueError("published pilot snapshot receipt changed during final archive verification")
            _check_same_path(
                archive_stage_fd,
                _SNAPSHOT_RECEIPT,
                receipt_fd,
                receipt_info,
                require_read_only=True,
            )
            if _hash_fd(
                attestation_fd,
                label="published pilot run-gate attestation",
            ) != hashlib.sha256(attestation_raw).hexdigest():
                raise ValueError("published pilot run-gate attestation changed during final archive verification")
            _check_same_path(
                archive_stage_fd,
                _SNAPSHOT_ATTESTATION,
                attestation_fd,
                attestation_info,
                require_read_only=True,
            )
            _verify_archived_run_gate_transcript(
                archive_stage_fd,
                receipt,
                _parse_snapshot_attestation(attestation_raw),
                archive_transcript_fd,
                archive_transcript_info,
            )
            _check_same_directory_path(
                archive_stage_fd,
                _SNAPSHOT_NAME,
                snapshot_fd,
                snapshot_info,
                require_read_only=True,
            )
            _check_same_directory_path(
                assay_fd,
                "b110-pilot-evidence-archive",
                archive_fd,
                archive_info,
                require_private_writable=True,
            )
            _check_same_directory_path(
                assay_fd,
                _SNAPSHOT_INCOMPLETE,
                incomplete_fd,
                incomplete_info,
                require_private_writable=True,
            )
            _check_same_absolute_directory_path(project, project_fd, project_info)
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            _check_same_directory_path(
                archive_stage_fd,
                _SNAPSHOT_NAME,
                snapshot_fd,
                snapshot_info,
                require_read_only=True,
            )
            # Confirm the published archive entry before the final snapshot
            # child pass, so a chmod injected at this boundary is caught by
            # the pass below.
            _check_same_directory_path(
                archive_fd,
                archive_name,
                archive_stage_fd,
                archive_stage_info,
                require_private_writable=True,
            )
            # Recheck every snapshot child after the receipt, attestation,
            # transcript, and parent-directory checks. A byte-preserving
            # chmod between earlier passes must still invalidate publication.
            _verify_snapshot_directory(
                snapshot_fd,
                retained_args,
                snapshot_sha256=receipt["snapshot_sha256"],
                require_active_deadlines=False,
            )
            # Recheck the .assay and archive names after the final child pass;
            # a concurrent replacement during that pass cannot leave success
            # bound only to an open, detached directory.
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            _check_same_absolute_directory_path(project, project_fd, project_info)
            _check_same_directory_path(
                archive_fd,
                archive_name,
                archive_stage_fd,
                archive_stage_info,
                require_private_writable=True,
            )
            _check_same_directory_path(
                assay_fd,
                "b110-pilot-evidence-archive",
                archive_fd,
                archive_info,
                require_private_writable=True,
            )
            _verify_snapshot_permissions(snapshot_fd, retained_args)
            if _hash_fd(
                receipt_fd,
                label="published pilot snapshot receipt",
            ) != hashlib.sha256(receipt_raw).hexdigest():
                raise ValueError("published pilot snapshot receipt changed during archive completion")
            _check_same_path(
                archive_stage_fd,
                _SNAPSHOT_RECEIPT,
                receipt_fd,
                receipt_info,
                require_read_only=True,
            )
            if _hash_fd(
                attestation_fd,
                label="published pilot run-gate attestation",
            ) != hashlib.sha256(attestation_raw).hexdigest():
                raise ValueError("published pilot run-gate attestation changed during archive completion")
            _check_same_path(
                archive_stage_fd,
                _SNAPSHOT_ATTESTATION,
                attestation_fd,
                attestation_info,
                require_read_only=True,
            )
            _verify_archived_run_gate_transcript(
                archive_stage_fd,
                receipt,
                attestation,
                archive_transcript_fd,
                archive_transcript_info,
            )
            # The permission sweep reads through pinned descriptors. Recheck
            # every advertised directory name after it so a replacement during
            # that sweep cannot leave an unverified bundle at the public path.
            _check_same_directory_path(
                assay_fd,
                "b110-pilot-evidence-archive",
                archive_fd,
                archive_info,
                require_private_writable=True,
            )
            _check_same_directory_path(
                archive_fd,
                archive_name,
                archive_stage_fd,
                archive_stage_info,
                require_private_writable=True,
            )
            _check_same_directory_path(
                archive_stage_fd,
                _SNAPSHOT_NAME,
                snapshot_fd,
                snapshot_info,
                require_read_only=True,
            )
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            _check_same_absolute_directory_path(project, project_fd, project_info)
        except (OSError, ValueError) as exc:
            try:
                quarantine_name = _withdraw_unverified_archive_entry(
                    archive_fd,
                    incomplete_fd,
                    archive_name,
                    assay_fd,
                )
            except (OSError, ValueError) as withdraw_exc:
                raise ValueError(
                    "pilot archive failed final verification at publication and the unverified entry "
                    f"could not be withdrawn: {withdraw_exc}"
                ) from exc
            try:
                _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            except (OSError, ValueError) as assay_path_exc:
                raise ValueError(
                    "pilot .assay path changed during archive withdrawal; the opened directory "
                    "is no longer at its advertised project path"
                ) from assay_path_exc
            withdrawal = (
                f"unverified entry moved to {quarantine_name}"
                if quarantine_name is not None
                else "unverified archive path was already absent"
            )
            raise ValueError(
                "pilot archive failed final verification at publication boundary; "
                f"{withdrawal}"
            ) from exc
        return "archived", archive_name
    finally:
        if archive_stage_fd >= 0:
            os.close(archive_stage_fd)
        if incomplete_fd >= 0:
            os.close(incomplete_fd)
        if archive_fd >= 0:
            os.close(archive_fd)
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if receipt_fd >= 0:
            os.close(receipt_fd)
        if attestation_fd >= 0:
            os.close(attestation_fd)
        if archive_transcript_fd >= 0:
            os.close(archive_transcript_fd)
        if assay_fd >= 0:
            os.close(assay_fd)
        os.close(project_fd)


def verify_and_publish(args: argparse.Namespace) -> str:
    if _HEX40.fullmatch(args.commit) is None or _HEX40.fullmatch(args.tree) is None:
        raise ValueError("expected source commit and tree must be full lowercase Git IDs")
    if _HEX64.fullmatch(args.manifest_sha256) is None:
        raise ValueError("reported attestation digest must be a lowercase SHA-256 digest")
    if args.container_exit != 0:
        raise ValueError("pilot container exit status is not zero")
    expected_campaign = f"b110-pilot-{args.commit[:12]}"
    if args.campaign != expected_campaign:
        raise ValueError("pilot campaign name does not match the expected source commit")

    project, project_fd, project_info = _open_expected_project(args)
    assay_fd = -1
    assay_info: os.stat_result | None = None
    live_state_fd = -1
    candidate_fd = -1
    manifest_fd = -1
    manifest_info: os.stat_result | None = None
    opened: dict[str, tuple[int, os.stat_result, str]] = {}
    staging_fd = -1
    staging_info: os.stat_result | None = None
    staging_name: str | None = None
    snapshot_sha256 = ""
    try:
        assay_fd = os.open(
            ".assay",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=project_fd,
        )
        assay_info = os.fstat(assay_fd)
        _check_expected_assay_identity(args, assay_info)
        live_state_fd = os.open(
            "b110-pilot-state",
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=assay_fd,
        )
        live_state_info = os.fstat(live_state_fd)
        candidate_fd, candidate_info = _open_regular(
            assay_fd, "b110-pilot-candidates.txt", label="pilot candidate file", limit=1024 * 1024
        )
        candidates_raw = _read_fd(candidate_fd, limit=1024 * 1024, label="pilot candidate file")
        candidate_ids = _candidate_ids(candidates_raw)
        expected = _expected_artifacts(candidate_ids, args.commit)
        expected_state_names = _check_state_inventory(
            live_state_fd, candidate_ids, label="live pilot state"
        )
        stale_evidence = _prior_evidence_names(assay_fd)
        if stale_evidence:
            raise ValueError(
                "prior pilot evidence remains after retry admission; refusing to replace it: "
                + ", ".join(stale_evidence)
            )

        manifest_fd, manifest_info = _open_regular(
            assay_fd, _INNER_MANIFEST, label="pilot attestation manifest", limit=4 * 1024 * 1024
        )
        manifest_raw = _read_fd(
            manifest_fd, limit=4 * 1024 * 1024, label="pilot attestation manifest"
        )
        if hashlib.sha256(manifest_raw).hexdigest() != args.manifest_sha256:
            raise ValueError("pilot manifest differs from the inner verification marker")
        manifest_entries = _parse_manifest(manifest_raw, expected, label="pilot attestation manifest")

        for relative in sorted(expected):
            if relative == ".assay/b110-pilot-candidates.txt":
                descriptor, info = candidate_fd, candidate_info
            elif relative.startswith(".assay/b110-pilot-state/"):
                descriptor, info = _open_regular(
                    live_state_fd,
                    relative.split("/", 2)[2],
                    label=f"pilot artifact {relative}",
                    limit=_FILE_LIMIT,
                )
            else:
                descriptor, info = _open_regular(
                    assay_fd,
                    relative.removeprefix(".assay/"),
                    label=f"pilot artifact {relative}",
                    limit=_FILE_LIMIT,
                )
            expected_digest = manifest_entries[relative]
            opened[relative] = (descriptor, info, expected_digest)
            if _hash_fd(descriptor, label=f"pilot artifact {relative}") != expected_digest:
                raise ValueError(f"retained pilot artifact differs from its manifest: {relative}")

        deadline_name = f"campaign-deadline-b110-pilot-{args.commit[:12]}.json"
        deadline_raw = _read_fd(
            opened[f".assay/{deadline_name}"][0], limit=1024 * 1024, label="campaign deadline"
        )
        window_raw = _read_fd(
            opened[".assay/b110-pilot-attempt-window.json"][0],
            limit=1024 * 1024,
            label="pilot attempt window",
        )
        _verify_deadlines(
            deadline_raw=deadline_raw,
            window_raw=window_raw,
            campaign=args.campaign,
            commit=args.commit,
            tree=args.tree,
        )

        staging_name = f".{_SNAPSHOT_NAME}.stage.{secrets.token_hex(12)}"
        os.mkdir(staging_name, 0o700, dir_fd=assay_fd)
        staging_fd = os.open(
            staging_name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=assay_fd,
        )
        staging_info = os.fstat(staging_fd)
        staging_state_fd = -1
        try:
            os.mkdir("b110-pilot-state", 0o700, dir_fd=staging_fd)
            staging_state_fd = os.open(
                "b110-pilot-state",
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=staging_fd,
            )
            staging_state_info = os.fstat(staging_state_fd)
            for relative in sorted(expected):
                destination_relative = relative.removeprefix(".assay/")
                if destination_relative.startswith("b110-pilot-state/"):
                    destination_parent = staging_state_fd
                    destination_name = destination_relative.split("/", 1)[1]
                else:
                    destination_parent = staging_fd
                    destination_name = destination_relative
                destination_fd = _create_snapshot_file(destination_parent, destination_name)
                try:
                    source_fd = opened[relative][0]
                    _copy_descriptor(
                        source_fd,
                        destination_fd,
                        expected_digest=manifest_entries[relative],
                        label=f"pilot artifact {relative}",
                    )
                    destination_info = os.fstat(destination_fd)
                    _check_same_path(destination_parent, destination_name, destination_fd, destination_info)
                finally:
                    os.close(destination_fd)

            _write_snapshot_bytes(
                staging_fd,
                _INNER_MANIFEST,
                manifest_raw,
                expected_digest=args.manifest_sha256,
            )
            snapshot_entries = {
                relative.removeprefix(".assay/"): digest
                for relative, digest in manifest_entries.items()
            }
            snapshot_entries[_INNER_MANIFEST] = args.manifest_sha256
            snapshot_raw = "".join(
                f"{digest}  {relative}\n" for relative, digest in sorted(snapshot_entries.items())
            ).encode("ascii")
            snapshot_sha256 = hashlib.sha256(snapshot_raw).hexdigest()
            _write_snapshot_bytes(
                staging_fd,
                _SNAPSHOT_INDEX,
                snapshot_raw,
                expected_digest=snapshot_sha256,
            )
            _make_snapshot_read_only(staging_fd, _snapshot_paths(expected))
            os.fsync(staging_state_fd)
            os.fsync(staging_fd)
        finally:
            if staging_state_fd >= 0:
                os.close(staging_state_fd)

        staging_values = vars(args).copy()
        staging_values["snapshot_sha256"] = snapshot_sha256
        staging_args = argparse.Namespace(**staging_values)
        _verify_snapshot_directory(staging_fd, staging_args, snapshot_sha256=snapshot_sha256)
        if _check_state_inventory(live_state_fd, candidate_ids, label="live pilot state") != expected_state_names:
            raise ValueError("live pilot state inventory changed during host snapshot creation")
        _check_same_path(assay_fd, _INNER_MANIFEST, manifest_fd, manifest_info)
        _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
        _check_same_directory_path(assay_fd, "b110-pilot-state", live_state_fd, live_state_info)
        _check_same_absolute_directory_path(project, project_fd, project_info)

        final_values = vars(args).copy()
        final_values["snapshot_sha256"] = snapshot_sha256
        final_args = argparse.Namespace(**final_values)
        receipt_raw = _snapshot_receipt_bytes(final_args, snapshot_sha256=snapshot_sha256)
        _write_snapshot_receipt_named(assay_fd, _SNAPSHOT_PENDING, receipt_raw)
        os.fsync(assay_fd)

        # The host snapshot is the authoritative retained evidence. The live
        # resumable files are no longer trusted after this atomic directory
        # publication, so racing writes cannot change what the marker binds.
        _rename_noreplace(
            staging_name,
            _SNAPSHOT_NAME,
            src_dir_fd=assay_fd,
            dst_dir_fd=assay_fd,
        )
        staging_name = None
        os.fsync(assay_fd)
        final_snapshot_fd = os.open(
            _SNAPSHOT_NAME,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=assay_fd,
        )
        try:
            final_info = os.fstat(final_snapshot_fd)
            if staging_info is None or (final_info.st_dev, final_info.st_ino) != (staging_info.st_dev, staging_info.st_ino):
                raise ValueError("published pilot snapshot does not match the verified staging directory")
            _check_same_directory_path(assay_fd, _SNAPSHOT_NAME, final_snapshot_fd, final_info)
            _check_same_directory_path(project_fd, ".assay", assay_fd, assay_info)
            _check_same_absolute_directory_path(project, project_fd, project_info)
        finally:
            os.close(final_snapshot_fd)

        _write_snapshot_receipt_named(assay_fd, _SNAPSHOT_RECEIPT, receipt_raw)
        os.fsync(assay_fd)
        verify_published_snapshot(final_args, allow_pending=True)
        pending_fd, pending_info, pending_raw = _read_named_receipt(
            assay_fd,
            _SNAPSHOT_PENDING,
            label="pending pilot snapshot receipt",
        )
        try:
            if pending_raw != receipt_raw:
                raise ValueError("pending pilot snapshot receipt changed during publication")
            _check_same_path(assay_fd, _SNAPSHOT_PENDING, pending_fd, pending_info)
            os.unlink(_SNAPSHOT_PENDING, dir_fd=assay_fd)
            os.fsync(assay_fd)
        finally:
            os.close(pending_fd)
        verify_published_snapshot(final_args)
        return snapshot_sha256
    finally:
        if staging_name is not None and staging_fd >= 0:
            try:
                _check_same_directory_path(assay_fd, staging_name, staging_fd, staging_info)
                _remove_directory_contents(staging_fd)
                os.rmdir(staging_name, dir_fd=assay_fd)
            except (OSError, ValueError):
                pass
        for descriptor, _info, _digest in opened.values():
            if descriptor != candidate_fd:
                os.close(descriptor)
        if candidate_fd >= 0:
            os.close(candidate_fd)
        if manifest_fd >= 0:
            os.close(manifest_fd)
        if staging_fd >= 0:
            os.close(staging_fd)
        if live_state_fd >= 0:
            os.close(live_state_fd)
        if assay_fd >= 0:
            os.close(assay_fd)
        os.close(project_fd)


def _check_same_path(
    directory_fd: int,
    name: str,
    descriptor: int,
    original: os.stat_result | None,
    *,
    require_read_only: bool = False,
) -> None:
    if original is None:
        raise ValueError(f"cannot pin identity for {name}")
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        _raise_filesystem_error(
            exc,
            label=f"pilot artifact path could not be checked during host verification: {name}",
        )
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino)
    ):
        raise ValueError(f"pilot artifact path changed during host verification: {name}")
    if require_read_only:
        _require_read_only(current, label=f"pilot artifact path {name}")
        _require_read_only(opened, label=f"pilot artifact descriptor {name}")


def _check_same_directory_path(
    parent_fd: int,
    name: str,
    descriptor: int,
    original: os.stat_result | None,
    *,
    require_read_only: bool = False,
    require_private_writable: bool = False,
) -> None:
    if original is None:
        raise ValueError(f"cannot pin identity for directory {name}")
    if require_read_only and require_private_writable:
        raise ValueError("directory path cannot be both read-only and writable")
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        _raise_filesystem_error(
            exc,
            label=f"pilot directory path could not be checked during host verification: {name}",
        )
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino)
    ):
        raise ValueError(f"pilot directory path changed during host verification: {name}")
    if require_read_only:
        _require_read_only(current, label=f"directory path {name}")
        _require_read_only(opened, label=f"directory descriptor {name}")
    if require_private_writable:
        expected_mode = stat.S_IMODE(original.st_mode)
        if (
            stat.S_IMODE(current.st_mode) != expected_mode
            or stat.S_IMODE(opened.st_mode) != expected_mode
        ):
            raise ValueError(f"pilot directory permissions changed during host verification: {name}")
        _require_private_writable_directory(current, label=f"directory path {name}")
        _require_private_writable_directory(opened, label=f"directory descriptor {name}")


def _check_same_absolute_directory_path(
    path: Path,
    descriptor: int,
    original: os.stat_result,
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        _raise_filesystem_error(
            exc,
            label=f"pilot project path could not be checked during host verification: {path}",
        )
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino)
    ):
        raise ValueError(f"pilot project path changed during host verification: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--campaign")
    parser.add_argument("--commit")
    parser.add_argument("--tree")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-tree")
    parser.add_argument("--expected-project-device", type=int, required=True)
    parser.add_argument("--expected-project-inode", type=int, required=True)
    parser.add_argument("--container-exit", type=int)
    parser.add_argument("--verify-published-snapshot", action="store_true")
    parser.add_argument("--archive-prior-snapshot", action="store_true")
    parser.add_argument("--clear-prior-outputs", choices=("b110-pilot", "b110-screen"))
    parser.add_argument("--expected-assay-device", type=int)
    parser.add_argument("--expected-assay-inode", type=int)
    parser.add_argument("--expected-assay-absent", action="store_true")
    parser.add_argument("--snapshot-sha256")
    args = parser.parse_args(argv)
    try:
        if args.clear_prior_outputs is not None:
            if (
                args.archive_prior_snapshot
                or args.verify_published_snapshot
                or args.container_exit is not None
                or args.snapshot_sha256 is not None
                or args.expected_commit is not None
                or args.expected_tree is not None
                or args.campaign is not None
                or args.commit is not None
                or args.tree is not None
                or args.manifest_sha256 is not None
            ):
                raise ValueError("stale-output cleanup accepts only the selected lane and project identities")
            removed, assay_device, assay_inode = clear_prior_outputs(args)
            print(f"B110_PRIOR_OUTPUTS_CLEARED={removed}")
            print(f"B110_ASSAY_STATE_IDENTITY={assay_device}:{assay_inode}")
        elif args.archive_prior_snapshot:
            if (
                args.verify_published_snapshot
                or args.expected_assay_absent
                or args.container_exit is not None
                or args.snapshot_sha256 is not None
                or args.expected_commit is None
                or args.expected_tree is None
            ):
                raise ValueError("prior snapshot archival requires the expected source identity only")
            if (args.expected_assay_device is None) != (args.expected_assay_inode is None):
                raise ValueError("prior snapshot archival requires both .assay identity fields or neither")
            if (
                args.expected_assay_device is not None
                and (args.expected_assay_device < 0 or args.expected_assay_inode is None or args.expected_assay_inode < 1)
            ):
                raise ValueError("prior snapshot archival received a malformed .assay identity")
            disposition, name = archive_prior_snapshot(args)
            if disposition == "archived":
                print(f"B110_PILOT_PRIOR_ARCHIVED={name}")
            elif disposition == "quarantined":
                print(f"B110_PILOT_PRIOR_QUARANTINED={name}")
            else:
                print("B110_PILOT_PRIOR_NONE=1")
        elif args.verify_published_snapshot:
            if (
                args.expected_assay_absent
                or any(value is None for value in (args.campaign, args.commit, args.tree, args.manifest_sha256))
            ):
                raise ValueError("snapshot verification requires campaign, source and manifest digests")
            if (args.expected_assay_device is None) != (args.expected_assay_inode is None):
                raise ValueError("snapshot verification requires both .assay identity fields or neither")
            if (
                args.expected_assay_device is not None
                and (args.expected_assay_device < 0 or args.expected_assay_inode is None or args.expected_assay_inode < 1)
            ):
                raise ValueError("snapshot verification received a malformed .assay identity")
            if args.container_exit is not None or args.snapshot_sha256 is None:
                raise ValueError("snapshot verification requires --snapshot-sha256 and no --container-exit")
            verified_snapshot_sha256 = verify_published_snapshot(args)
            print(f"B110_PILOT_HOST_VERIFIED_SHA256={verified_snapshot_sha256}")
        else:
            if (
                args.expected_assay_absent
                or any(value is None for value in (args.campaign, args.commit, args.tree, args.manifest_sha256))
            ):
                raise ValueError("snapshot publication requires campaign, source and manifest digests")
            if (args.expected_assay_device is None) != (args.expected_assay_inode is None):
                raise ValueError("snapshot publication requires both .assay identity fields or neither")
            if (
                args.expected_assay_device is not None
                and (args.expected_assay_device < 0 or args.expected_assay_inode is None or args.expected_assay_inode < 1)
            ):
                raise ValueError("snapshot publication received a malformed .assay identity")
            if args.container_exit is None:
                raise ValueError("snapshot publication requires --container-exit")
            snapshot_sha256 = verify_and_publish(args)
            print(f"B110_PILOT_SNAPSHOT_SHA256={snapshot_sha256}")
    except HistoryUnavailableError as exc:
        print(f"b110_pilot_host_check: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print(f"b110_pilot_host_check: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
