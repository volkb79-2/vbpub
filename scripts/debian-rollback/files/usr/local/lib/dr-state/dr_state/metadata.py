"""Sidecars, format versions, and the durable state sequence."""

from __future__ import annotations

import base64
import binascii
import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from . import FORMAT
from .errors import FormatError, Refused
from .ids import StateId
from .lock import FileLock, atomic_write
from .paths import Paths

SIDECAR_SCHEMA = 2
_HEADER_RE = re.compile(r"schema=(?P<schema>[0-9]+)\Z")
_KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_SEQUENCE_RE = re.compile(r"[0-9]{6}\Z")
_FORMAT_RE = re.compile(r"[0-9]+\Z")


@dataclass(frozen=True, slots=True)
class Metadata:
    """The fields carried by a schema-1 or schema-2 sidecar."""

    schema: int = SIDECAR_SCHEMA
    state: str = ""
    sequence: int = 0
    origin_slug: str = ""
    created_utc: str = ""
    parent_state: str = ""
    source: str = ""
    reason: str = ""
    note: str = ""
    os_pretty_name: str = ""
    os_id: str = ""
    os_version_id: str = ""
    kernel: str = ""
    packages_status: str = ""
    packages: str = ""
    apt_actions: str = ""
    kind: str = "checkpoint"
    pinned: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"live", "checkpoint"}:
            raise ValueError(f"invalid metadata kind: {self.kind!r}")
        if not isinstance(self.pinned, bool):
            raise ValueError("metadata pinned must be boolean")


@dataclass(frozen=True, slots=True)
class CounterRead:
    """The usable counter value and, separately, why it is unusable."""

    value: int | None
    issue: str | None = None


@dataclass(frozen=True, slots=True)
class Allocation:
    """One durable reservation and the repair that preceded it."""

    sequence: int
    floor: int
    repaired: bool
    warning: str | None


@dataclass(frozen=True, slots=True)
class FormatInfo:
    """A global format file as read, including read-only warnings."""

    version: int
    exists: bool
    issue: str | None = None


def sidecar_path(paths: Paths, state: StateId) -> Path:
    """Return the sidecar path for a managed state."""

    return paths.metadata_dir / f"{state.name}.meta"


def _decode_fields(data: bytes) -> tuple[int, dict[str, str]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FormatError(f"sidecar is not UTF-8: {error}") from error
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise FormatError("sidecar is empty")
    header = _HEADER_RE.fullmatch(lines[0])
    if header is None:
        raise FormatError("sidecar does not begin with schema=N")
    schema = int(header["schema"])
    if schema < 1:
        raise FormatError(f"invalid sidecar schema {schema}")
    fields: dict[str, str] = {}
    for number, line in enumerate(lines[1:], start=2):
        key, separator, encoded = line.partition("=")
        if not separator or _KEY_RE.fullmatch(key) is None:
            raise FormatError(f"sidecar line {number} is not key=base64(value)")
        if key in fields:
            raise FormatError(f"sidecar repeats key {key!r}")
        try:
            decoded_bytes = base64.b64decode(encoded, validate=True)
            value = decoded_bytes.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as error:
            raise FormatError(
                f"sidecar line {number} has an invalid base64 UTF-8 value: {error}"
            ) from error
        fields[key] = value
    return schema, fields


def parse_sidecar(data: bytes) -> Metadata:
    """Parse supported sidecar fields; future schemas remain readable."""

    schema, fields = _decode_fields(data)
    sequence_text = fields.get("sequence", "0")
    if not sequence_text.isdecimal():
        raise FormatError(f"sidecar sequence is not decimal: {sequence_text!r}")
    sequence = int(sequence_text)
    if not 0 <= sequence <= 999_999:
        raise FormatError(f"sidecar sequence is out of range: {sequence_text!r}")

    source = fields.get("source", "")
    if schema == 1:
        kind = "checkpoint"
        pinned = source == "install"
    elif schema >= 2:
        if schema == 2 and ("kind" not in fields or "pinned" not in fields):
            raise FormatError("schema-2 sidecar lacks kind or pinned")
        kind = fields.get("kind", "checkpoint")
        if kind not in {"live", "checkpoint"}:
            raise FormatError(f"sidecar kind is invalid: {kind!r}")
        pinned_text = fields.get("pinned", "0")
        if pinned_text not in {"0", "1"}:
            raise FormatError(f"sidecar pinned value is invalid: {pinned_text!r}")
        pinned = pinned_text == "1"

    return Metadata(
        schema=schema,
        state=fields.get("state", ""),
        sequence=sequence,
        origin_slug=fields.get("origin_slug", ""),
        created_utc=fields.get("created_utc", ""),
        parent_state=fields.get("parent_state", ""),
        source=source,
        reason=fields.get("reason", ""),
        note=fields.get("note", ""),
        os_pretty_name=fields.get("os_pretty_name", ""),
        os_id=fields.get("os_id", ""),
        os_version_id=fields.get("os_version_id", ""),
        kernel=fields.get("kernel", ""),
        packages_status=fields.get("packages_status", ""),
        packages=fields.get("packages", ""),
        apt_actions=fields.get("apt_actions", ""),
        kind=kind,
        pinned=pinned,
    )


def _metadata_fields(metadata: Metadata) -> tuple[tuple[str, str], ...]:
    if not 1 <= metadata.sequence <= 999_999:
        raise FormatError(f"cannot write sidecar sequence {metadata.sequence}")
    return (
        ("state", metadata.state),
        ("sequence", f"{metadata.sequence:06d}"),
        ("origin_slug", metadata.origin_slug),
        ("created_utc", metadata.created_utc),
        ("parent_state", metadata.parent_state),
        ("source", metadata.source),
        ("reason", metadata.reason),
        ("note", metadata.note),
        ("os_pretty_name", metadata.os_pretty_name),
        ("os_id", metadata.os_id),
        ("os_version_id", metadata.os_version_id),
        ("kernel", metadata.kernel),
        ("packages_status", metadata.packages_status),
        ("packages", metadata.packages),
        ("apt_actions", metadata.apt_actions),
        ("kind", metadata.kind),
        ("pinned", "1" if metadata.pinned else "0"),
    )


def serialize_sidecar(metadata: Metadata) -> bytes:
    """Encode all known fields in the schema-2 sidecar format."""

    lines = [f"schema={SIDECAR_SCHEMA}"]
    for key, value in _metadata_fields(metadata):
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        lines.append(f"{key}={encoded}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_sidecar(path: str | Path) -> Metadata | None:
    """Read one sidecar; a missing file is absent, a symlink is invalid."""

    sidecar = Path(path)
    try:
        status = os.lstat(sidecar)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(status.st_mode):
        raise FormatError(f"sidecar {sidecar} is a symlink")
    if not stat.S_ISREG(status.st_mode):
        raise FormatError(f"sidecar {sidecar} is not a regular file")
    return parse_sidecar(sidecar.read_bytes())


def _require_writable_directory(directory: Path) -> None:
    try:
        status = os.lstat(directory)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(status.st_mode):
        raise FormatError(f"control directory {directory} is a symlink")
    if not stat.S_ISDIR(status.st_mode):
        raise FormatError(f"control directory {directory} is not a directory")


def write_sidecar(paths: Paths, state: StateId, metadata: Metadata) -> None:
    """Atomically write schema 2, refusing an existing newer sidecar."""

    _require_writable_directory(paths.control_dir)
    _require_writable_directory(paths.metadata_dir)
    target = sidecar_path(paths, state)
    try:
        status = os.lstat(target)
    except FileNotFoundError:
        status = None
    if status is not None:
        if stat.S_ISLNK(status.st_mode):
            pass
        elif not stat.S_ISREG(status.st_mode):
            raise FormatError(f"sidecar target {target} is not a regular file")
        else:
            existing = load_sidecar(target)
            if existing is not None and existing.schema > SIDECAR_SCHEMA:
                raise FormatError(
                    f"sidecar {target} uses schema {existing.schema}; "
                    f"this runtime writes schema {SIDECAR_SCHEMA}"
                )
    atomic_write(target, serialize_sidecar(replace(metadata, schema=SIDECAR_SCHEMA)))


def scan_sidecar_sequences(
    paths: Paths,
    warn: Callable[[str], None] | None = None,
) -> tuple[int, ...]:
    """Return sequences from usable sidecars, warning instead of failing."""

    sequences: list[int] = []
    try:
        entries = sorted(paths.metadata_dir.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        return ()
    for path in entries:
        if not path.name.endswith(".meta"):
            continue
        try:
            metadata = load_sidecar(path)
        except (FormatError, OSError) as error:
            if warn is not None:
                warn(f"ignoring unusable sidecar {path}: {error}")
            continue
        if metadata is not None and metadata.sequence:
            sequences.append(metadata.sequence)
    return tuple(sequences)


def read_counter(path: str | Path) -> CounterRead:
    """Read a six-digit counter without treating corruption as an ID."""

    counter = Path(path)
    try:
        status = os.lstat(counter)
    except FileNotFoundError:
        return CounterRead(None, f"counter {counter} is missing")
    if stat.S_ISLNK(status.st_mode):
        return CounterRead(None, f"counter {counter} is a symlink")
    if not stat.S_ISREG(status.st_mode):
        return CounterRead(None, f"counter {counter} is not a regular file")
    try:
        value = counter.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        return CounterRead(None, f"counter {counter} is unreadable: {error}")
    if value.endswith("\n"):
        value = value[:-1]
    if _SEQUENCE_RE.fullmatch(value) is None or value == "000000":
        return CounterRead(None, f"counter {counter} is malformed")
    return CounterRead(int(value))


def counter_floor(
    counter: int | None,
    states: Iterable[StateId],
    sidecar_sequences: Iterable[int],
) -> int:
    """Return the highest sequence already named by any durable source."""

    named = [state.sequence for state in states]
    sidecars = [sequence for sequence in sidecar_sequences if sequence > 0]
    return max([counter or 0, *named, *sidecars])


def allocate_sequence(
    paths: Paths,
    states: Iterable[StateId],
    *,
    lock: FileLock,
    warn: Callable[[str], None] | None = None,
) -> Allocation:
    """Reserve above every known sequence while holding the writer lock."""

    if lock.path != paths.lock_file:
        raise RuntimeError(f"writer lock {lock.path} does not protect {paths.lock_file}")
    lock.require_exclusive()
    _require_writable_directory(paths.control_dir)

    warnings: list[str] = []

    def remember(message: str) -> None:
        warnings.append(message)
        if warn is not None:
            warn(message)

    sidecar_sequences = scan_sidecar_sequences(paths, remember)
    counter = read_counter(paths.counter_file)
    if counter.issue is not None:
        remember(counter.issue)
    floor = counter_floor(counter.value, states, sidecar_sequences)
    if floor >= 999_999:
        raise Refused("state sequence exhausted at 999999")
    sequence = floor + 1
    repaired = counter.value != floor
    if repaired and counter.issue is None:
        remember(f"counter {paths.counter_file} is below floor {floor}")
    atomic_write(paths.counter_file, f"{sequence:06d}\n")
    warning = "; ".join(warnings) or None
    return Allocation(sequence, floor, repaired, warning)


def read_format(path: str | Path) -> FormatInfo:
    """Read a global format file; future values remain available to callers."""

    format_path = Path(path)
    try:
        status = os.lstat(format_path)
    except FileNotFoundError:
        return FormatInfo(FORMAT, exists=False)
    if stat.S_ISLNK(status.st_mode):
        return FormatInfo(FORMAT, exists=True, issue=f"format file {format_path} is a symlink")
    if not stat.S_ISREG(status.st_mode):
        return FormatInfo(FORMAT, exists=True, issue=f"format file {format_path} is not regular")
    try:
        text = format_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        return FormatInfo(FORMAT, exists=True, issue=f"cannot read format {format_path}: {error}")
    scalar = text[:-1] if text.endswith("\n") else text
    if not scalar or "\n" in scalar or _FORMAT_RE.fullmatch(scalar) is None:
        return FormatInfo(FORMAT, exists=True, issue=f"format file {format_path} is malformed")
    version = int(scalar)
    if version < 1:
        return FormatInfo(version, exists=True, issue=f"format {version} is invalid")
    if version > FORMAT:
        return FormatInfo(
            version,
            exists=True,
            issue=f"format {version} is newer than supported format {FORMAT}",
        )
    return FormatInfo(version, exists=True)


def require_writable_format(path: str | Path) -> FormatInfo:
    """Return a format's state or refuse writes for an invalid/newer one."""

    info = read_format(path)
    if info.issue is not None:
        raise FormatError(info.issue)
    return info


def _write_format(path: Path) -> None:
    require_writable_format(path)
    atomic_write(path, f"{FORMAT}\n")


def write_control_format(paths: Paths) -> None:
    """Write the control-directory format without following a symlinked control."""

    _require_writable_directory(paths.control_dir)
    _write_format(paths.control_format_file)


def write_esp_format(paths: Paths) -> None:
    """Write the ESP format file."""

    _write_format(paths.esp_format_file)
