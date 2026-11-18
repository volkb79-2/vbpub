"""The hostobs/1 BLOCK codec, the efficient decoder of WIRE.md section 7.4.

Standard library only. ``decode_block(buf, held)`` validates one complete BLOCK frame and
returns a :class:`Block` whose sections are views over ``buf``; no Python bytecode runs per
value during decoding or validation (C-speed O(n) work remains). Every refusal is a
:class:`WireError` whose ``kind`` is one of the 46 decoder kinds of WIRE.md section 3.10 and
whose ``detail`` carries the same keys as the fixtures' ``expect_error``.

Byte order: the cast views of section 7.4 need a little-endian host. On a big-endian host
(``sys.byteorder == 'big'``) every accessed array is one ``array.array`` copy that is
byte-swapped; that is the only copying path.
"""

from __future__ import annotations

import array
import struct
import sys
from functools import lru_cache
from typing import NamedTuple

MAX_FRAME_BYTES = 16 * 1024 * 1024
FRAME_HEADER_LEN = 12
HEADER_BODY_LEN = 80
MAX_ENTRY_TEXT = 256
MAX_DICTIONARY_ENTRIES = 65_536
#: A stream retains the previous validated rows of one frame only up to this size (WIRE.md 7.4).
RETAIN_LIMIT = 1024 * 1024

FRAME_TYPE_NAMES = {1: "req", 2: "rep", 3: "evt", 4: "dict", 5: "block", 6: "end"}
QUALITY_NAMES = (
    "ok",
    "absent",
    "unreadable",
    "stale",
    "reset",
    "not-sampled",
    "unsupported",
    "unknown-topology",
)

#: The 46 decoder error kinds (``ProtoError::kind`` of ``crates/proto/src/error.rs``).
ERROR_KINDS = frozenset(
    (
        "truncated", "oversized-frame", "non-zero-reserved", "unknown-frame-type", "unknown-frame-flags",
        "unknown-mode", "unsupported-version", "section-reserved", "unsupported-required-section",
        "unsupported-section-version", "unknown-section-flags", "duplicate-section", "missing-section",
        "section-length", "trailing-bytes", "unknown-value-type", "unknown-column-kind", "column-reserved",
        "conflicting-column", "unknown-quality", "unknown-aggregation", "row-count-overflow", "length-overflow",
        "dictionary-entry-too-long", "dictionary-entry-not-utf8", "unknown-dictionary-kind",
        "dictionary-entry-unknown-code", "dictionary-entry-mask", "duplicate-dictionary-entry",
        "dictionary-entry-reserved", "dictionary-too-large", "dangling-dictionary-ref",
        "dictionary-entry-control", "generation-exhausted", "stale-dictionary-generation", "rows-and-time",
        "no-rows-or-time", "row-reference-not-entity", "column-reference-not-metric", "column-kind-mismatch",
        "not-a-block", "section-order", "aggregation-not-permitted", "subject-without-time", "deadline",
        "generation-regressed",
    )
)


class HostobsError(Exception):
    """Base of every error this package raises or returns."""


class WireError(HostobsError):
    """A decoder refusal: ``kind`` is a WIRE.md 3.10 name, ``detail`` the fixture's fields."""

    def __init__(self, kind: str, detail: dict | None = None) -> None:
        if kind not in ERROR_KINDS:
            raise ValueError(f"{kind!r} is not a decoder error kind")
        super().__init__(kind)
        self.kind = kind
        self.detail = detail if detail is not None else {}

    def __str__(self) -> str:
        if not self.detail:
            return self.kind
        return f"{self.kind}({', '.join(f'{k}={v}' for k, v in sorted(self.detail.items()))})"


class ServerError(HostobsError):
    """A wire error code (WIRE.md section 10) the server answered with."""

    def __init__(self, code: str, message: str = "", detail: dict | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.detail = detail if detail is not None else {}


class ConnectionLost(HostobsError):
    """EOF or reset on the connection (not a decoder or server verdict)."""


def _err(kind: str, **detail: object) -> WireError:
    return WireError(kind, detail)


def _align8(value: int) -> int:
    return (value + 7) & ~7


if struct.calcsize("<I") != 4 or struct.calcsize("I") != 4 or struct.calcsize("Q") != 8:  # pragma: no cover
    raise ImportError("hostobs_client needs 4-byte 'I' and 8-byte 'Q' native formats")
if array.array("I").itemsize != 4 or array.array("Q").itemsize != 8 or array.array("H").itemsize != 2:  # pragma: no cover
    raise ImportError("hostobs_client needs 2/4/8-byte array typecodes H/I/Q")

#: Zero-copy cast views are valid on little-endian hosts only (WIRE.md 7.4).
_NATIVE_VIEWS = sys.byteorder == "little"


def _view(mv: memoryview, fmt: str) -> memoryview | array.array:
    """The typed view of ``mv`` (a ``B`` slice): a cast on little-endian, else one swapped copy."""
    if _NATIVE_VIEWS:
        return mv.cast(fmt)
    arr = array.array(fmt, bytes(mv))
    if sys.byteorder == "big":
        arr.byteswap()
    return arr


# --------------------------------------------------------------------------- frame header

_FRAME_HDR = struct.Struct("<IBBHI")
_DIR_HDR = struct.Struct("<HH")
_HEADER_BODY = struct.Struct("<16sQQQQQqIIII")
_COL_HDR = struct.Struct("<IBBH")


def _short_header(buf: memoryview | bytes | bytearray, have: int) -> None:
    """Judge a buffer shorter than 12 bytes in wire order; always raises (WIRE.md 3.10)."""

    def need(end: int) -> None:
        if have < end:
            raise _err("truncated", context="frame header", need=end, have=have)

    need(4)
    need(5)
    if buf[4] not in FRAME_TYPE_NAMES:
        raise _err("unknown-frame-type", code=buf[4])
    need(6)
    flags = buf[5]
    if flags & ~1 or (flags & 1 and buf[4] != 5):
        raise _err("unknown-frame-flags", flags=flags)
    need(8)
    reserved = buf[6] | (buf[7] << 8)
    if reserved:
        raise _err("non-zero-reserved", value=reserved)
    need(FRAME_HEADER_LEN)
    raise AssertionError("unreachable: a buffer below 12 bytes failed no check")  # pragma: no cover


def parse_frame_header(buf, limit: int = MAX_FRAME_BYTES) -> tuple[int, int, int, int]:
    """Validate the 12-byte header: ``(length, type_code, flags, corr)``.

    The fixed fields are judged in wire order, then ``12 <= length <= min(limit, 16 MiB)``; an
    oversized length names ``limit`` (the reader's bound) as the maximum.
    """
    if len(buf) < FRAME_HEADER_LEN:
        _short_header(buf, len(buf))
    length, code, flags, reserved, corr = _FRAME_HDR.unpack_from(buf)
    if code not in FRAME_TYPE_NAMES:
        raise _err("unknown-frame-type", code=code)
    if flags & ~1 or (flags & 1 and code != 5):
        raise _err("unknown-frame-flags", flags=flags)
    if reserved:
        raise _err("non-zero-reserved", value=reserved)
    if length > MAX_FRAME_BYTES or length > limit:
        raise _err("oversized-frame", len=length, max=limit)
    if length < FRAME_HEADER_LEN:
        raise _err(
            "truncated", context="frame length shorter than the header", need=FRAME_HEADER_LEN, have=length
        )
    return length, code, flags, corr


# --------------------------------------------------------------------------- dictionary

#: Dictionary entry kinds.
ENTITY, METRIC, LABEL, FREE = 0, 1, 2, 3
_UNIT_MAX, _SCOPE_MAX, _SOURCE_MAX = 8, 3, 4
#: The aggregation mask a metric kind derives (WIRE.md 3.7): gauge, counter, state.
_MASKS = (0b111010, 0b000111, 0b000010)
_ALLOWED_AGG = (bytes((1, 3, 4, 5)), bytes((0, 1, 2)), bytes((1,)))  # by column kind 0 gauge/1 counter/2 state
_VALID_AGG = bytes(range(6))
_VALID_QUALITY = bytes(range(8))
_ZERO = b"\x00"
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_U32x2 = struct.Struct("<II")


class MetricFacts(NamedTuple):
    """A metric entry's codes (unit, kind 0 gauge/1 counter/2 state, scope, source)."""

    unit: int
    kind: int
    scope: int
    source: int


class Entry(NamedTuple):
    """One dictionary entry in a readable form (``Dictionary.entry``)."""

    kind: int
    text: str
    inc: int | None
    facts: MetricFacts | None
    label_kind: int | None
    label_source: int | None


def _trunc(need: int, have: int) -> WireError:
    return _err("truncated", context="dictionary section", need=need, have=have)


class Dictionary:
    """One dictionary generation. Entry ``i`` is index ``i``.

    ``kinds`` is the per-generation ``bytes`` kind map (0 entity, 1 metric, 2 label, 3 free) and
    ``metric_kinds`` the column kind each metric index declares (255 for a non-metric); the
    decoder validates references against them with C-level calls only. ``_rows_ok`` is the
    previous rows body validated against this dictionary (a view pinning that frame, released
    by the next validated frame, a decoder error and ``StreamDicts.close``).
    """

    __slots__ = ("generation", "kinds", "metric_kinds", "_texts", "_extra", "_metrics", "_raw", "_rows_ok")

    def __init__(self, generation, kinds, metric_kinds, texts, extra, metrics, raw) -> None:
        self.generation = generation
        self.kinds = kinds
        self.metric_kinds = metric_kinds
        self._texts = texts
        self._extra = extra
        self._metrics = metrics
        self._raw = raw
        self._rows_ok = None

    def __len__(self) -> int:
        return len(self.kinds)

    def text(self, index: int) -> str:
        return self._texts[index]

    def metric_index(self, name: str) -> int | None:
        """The index of metric ``name``, or ``None``."""
        return self._metrics.get(name)

    def entry(self, index: int) -> Entry:
        kind = self.kinds[index]
        extra = self._extra[index]
        text = self._texts[index]
        if kind == ENTITY:
            return Entry(kind, text, extra, None, None, None)
        if kind == METRIC:
            return Entry(kind, text, None, MetricFacts(*extra), None, None)
        if kind == LABEL:
            return Entry(kind, text, None, None, extra[0], extra[1])
        return Entry(kind, text, None, None, None, None)

    def entries(self) -> list[Entry]:
        return [self.entry(i) for i in range(len(self))]

    @classmethod
    def decode(cls, body) -> Dictionary:
        """Decode a dictionary section body (WIRE.md 3.7), refusing exactly what Rust refuses."""
        data = bytes(body)
        length = len(data)
        if length < 4:
            raise _trunc(4, length)
        (generation,) = _U32.unpack_from(data, 0)
        if length < 8:
            raise _trunc(8, length)
        (count,) = _U32.unpack_from(data, 4)
        if count > MAX_DICTIONARY_ENTRIES:
            raise _err("dictionary-too-large", count=count, max=MAX_DICTIONARY_ENTRIES)
        pos = 8
        kinds: list[int] = []
        mkinds: list[int] = []
        texts: list[str] = []
        extras: list = []
        metrics: dict[str, int] = {}
        seen: set = set()
        for index in range(count):
            if pos + 1 > length:
                raise _trunc(pos + 1, length)
            kind = data[pos]
            if pos + 2 > length:
                raise _trunc(pos + 2, length)
            flags = data[pos + 1]
            if flags:
                raise _err("dictionary-entry-reserved", index=index, flags=flags)
            if pos + 4 > length:
                raise _trunc(pos + 4, length)
            (text_len,) = _U16.unpack_from(data, pos + 2)
            if text_len > MAX_ENTRY_TEXT:
                raise _err("dictionary-entry-too-long", index=index, len=text_len)
            if kind > FREE:
                raise _err("unknown-dictionary-kind", index=index, code=kind)
            pos += 4
            mkind = 255
            if kind == ENTITY:
                if pos + 8 > length:
                    raise _trunc(pos + 8, length)
                extra = _U64.unpack_from(data, pos)[0]
                pos += 8
            elif kind == METRIC:
                if pos + 1 > length:
                    raise _trunc(pos + 1, length)
                unit = data[pos]
                if unit > _UNIT_MAX:
                    raise _err("dictionary-entry-unknown-code", index=index, code=unit)
                if pos + 2 > length:
                    raise _trunc(pos + 2, length)
                mkind = data[pos + 1]
                if mkind > 2:
                    raise _err("unknown-column-kind", code=mkind)
                if pos + 3 > length:
                    raise _trunc(pos + 3, length)
                scope = data[pos + 2]
                if scope > _SCOPE_MAX:
                    raise _err("dictionary-entry-unknown-code", index=index, code=scope)
                if pos + 4 > length:
                    raise _trunc(pos + 4, length)
                source = data[pos + 3]
                if source > _SOURCE_MAX:
                    raise _err("dictionary-entry-unknown-code", index=index, code=source)
                if pos + 6 > length:
                    raise _trunc(pos + 6, length)
                (mask,) = _U16.unpack_from(data, pos + 4)
                if mask != _MASKS[mkind]:
                    raise _err("dictionary-entry-mask", index=index, mask=mask, expected=_MASKS[mkind])
                extra = (unit, mkind, scope, source)
                pos += 6
            elif kind == LABEL:
                if pos + 1 > length:
                    raise _trunc(pos + 1, length)
                if pos + 2 > length:
                    raise _trunc(pos + 2, length)
                extra = (data[pos], data[pos + 1])
                pos += 2
            else:
                extra = None
            end = pos + text_len
            if end > length:
                raise _trunc(end, length)
            raw = data[pos:end]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise _err("dictionary-entry-not-utf8", index=index) from None
            pos = end
            if kind == ENTITY:
                identity = (0, extra)
            elif kind == METRIC:
                identity = (1, text)
            elif kind == LABEL:
                identity = (2, extra[0], extra[1], text)
            else:
                identity = (3, text)
            if identity in seen:
                raise _err("duplicate-dictionary-entry", index=index)
            if raw and (min(raw) < 0x20 or 0x7F in raw):
                offset = next(i for i, b in enumerate(raw) if b < 0x20 or b == 0x7F)
                raise _err("dictionary-entry-control", index=index, offset=offset)
            seen.add(identity)
            if kind == METRIC:
                metrics[text] = index
            kinds.append(kind)
            mkinds.append(mkind)
            texts.append(text)
            extras.append(extra)
        target = _align8(pos)
        if target > length:
            raise _trunc(length + 1, length)
        pad = data[pos:target].translate(None, _ZERO)
        if pad:
            raise _err("non-zero-reserved", value=pad[0])
        if target != length:
            raise _err("trailing-bytes", extra=length - target)
        dictionary = cls(generation, bytes(kinds), bytes(mkinds), texts, extras, metrics, data)
        return dictionary


# --------------------------------------------------------------------------- block objects


class BlockHeader(NamedTuple):
    """The 80-byte header body (WIRE.md 3.3); ``boot_id`` is the raw 16 bytes."""

    boot_id: bytes
    epoch: int
    seq: int
    mono_start_ns: int
    mono_end_ns: int
    boot_ns: int
    real_ns: int
    dict_gen: int
    n_rows: int
    dropped: int
    flags: int


def boot_id_text(raw: bytes) -> str:
    """The dashed UUID text of a header ``boot_id``."""
    digits = bytes(raw).hex()
    return f"{digits[0:8]}-{digits[8:12]}-{digits[12:16]}-{digits[16:20]}-{digits[20:32]}"


class Rows:
    """A live block's rows body: ``ents()`` and ``t_off_us()`` u32 views (WIRE.md 3.4)."""

    __slots__ = ("_mv", "n_rows")

    def __init__(self, mv: memoryview, n_rows: int) -> None:
        self._mv = mv
        self.n_rows = n_rows

    def ents(self):
        return _view(self._mv[: 4 * self.n_rows], "I")

    def t_off_us(self):
        return _view(self._mv[4 * self.n_rows : 8 * self.n_rows], "I")


class Time:
    """A history block's time body (WIRE.md 3.6); one view per field."""

    __slots__ = ("_mv", "n")

    def __init__(self, mv: memoryview, n: int) -> None:
        self._mv = mv
        self.n = n

    def real_ns(self):
        return _view(self._mv[: 8 * self.n], "q")

    def covered_ms(self):
        return _view(self._mv[8 * self.n : 12 * self.n], "I")

    def samples(self):
        return _view(self._mv[12 * self.n : 14 * self.n], "H")

    def agg(self) -> memoryview:
        return self._mv[14 * self.n : 15 * self.n]


_TYPECODES = {1: "Q", 2: "q", 3: "d"}
VALUE_TYPE_NAMES = {1: "u64", 2: "i64", 3: "f64"}
KIND_NAMES = {0: "gauge", 1: "counter", 2: "state"}


class Column:
    """One metric's values and quality (WIRE.md 3.5). Values are exact ``int`` for u64/i64."""

    __slots__ = ("metric", "value_type", "kind", "n_rows", "_mv", "_raw", "_quality", "_name")

    def __init__(self, metric: int, value_type: int, kind: int, n_rows: int, mv: memoryview, name) -> None:
        self.metric = metric
        self.value_type = value_type
        self.kind = kind
        self.n_rows = n_rows
        self._mv = mv
        self._raw = None
        self._quality = None
        self._name = name

    @property
    def name(self) -> str | None:
        """The metric name when a dictionary was in force, else ``None``."""
        return self._name

    @property
    def raw_values(self):
        """The 8-byte values as a typed view; rows whose quality is not 0 hold placeholders."""
        raw = self._raw
        if raw is None:
            raw = self._raw = _view(self._mv[8 : 8 + 8 * self.n_rows], _TYPECODES[self.value_type])
        return raw

    @property
    def quality(self) -> memoryview:
        """The u8 quality codes (WIRE.md 3.9)."""
        quality = self._quality
        if quality is None:
            start = 8 + 8 * self.n_rows
            quality = self._quality = self._mv[start : start + self.n_rows]
        return quality

    def measured(self, i: int):
        """The value of row ``i`` when its quality is ``ok``, else ``None``."""
        if self.quality[i]:
            return None
        return self.raw_values[i]

    def measured_list(self) -> list:
        """Every row's measurement, ``None`` for a non-ok row (an O(n) materialisation)."""
        values = self.raw_values.tolist()
        quality = bytes(self.quality)
        if not any(quality):
            return values
        return [None if q else v for v, q in zip(values, quality)]

    def quality_name(self, i: int) -> str:
        return QUALITY_NAMES[self.quality[i]]


class Block:
    """A validated BLOCK frame. ``buf`` is owned by the block: views reference it."""

    __slots__ = (
        "corr", "header", "dictionary", "dictionary_in_force", "subject", "skipped", "directory_kinds",
        "buf", "_mv", "_rows_at", "_time_at", "_cols",
    )

    @property
    def rows(self) -> Rows | None:
        at = self._rows_at
        if at is None:
            return None
        return Rows(self._mv[at : at + 8 * self.header.n_rows], self.header.n_rows)

    @property
    def time(self) -> Time | None:
        at = self._time_at
        if at is None:
            return None
        n = self.header.n_rows
        return Time(self._mv[at : at + _align8(15 * n)], n)

    def _column(self, metric: int) -> Column:
        _type, kind, offset = self._cols[metric]
        n = self.header.n_rows
        d = self.dictionary_in_force
        name = d.text(metric) if d is not None and metric < len(d) else None
        return Column(metric, _type, kind, n, self._mv[offset : offset + _align8(8 + 9 * n)], name)

    def columns(self):
        """The columns in directory order, built lazily."""
        for metric in self._cols:
            yield self._column(metric)

    def column(self, metric_name: str) -> Column:
        """The column of metric ``metric_name`` (``KeyError`` when the block has none)."""
        d = self.dictionary_in_force
        if d is None:
            raise LookupError("this block was decoded without a dictionary; use the metric index")
        index = d.metric_index(metric_name)
        if index is None or index not in self._cols:
            raise KeyError(metric_name)
        return self._column(index)

    def column_by_index(self, metric: int) -> Column:
        return self._column(metric)


# --------------------------------------------------------------------------- decoder

_RANK = {1: 0, 5: 1, 6: 2, 2: 3, 4: 3, 3: 4}
_ONE_OF = (1, 2, 4, 5, 6)


@lru_cache(maxsize=64)
def _dir_struct(n_sec: int) -> struct.Struct:
    return struct.Struct("<" + "HBBI" * n_sec)


def decode_block(buf, held: Dictionary | None = None) -> Block:
    """Decode and validate one complete BLOCK frame (WIRE.md 3.10 order, section 7.4 path).

    ``held`` is the stream's held dictionary: a bare block is judged against it and, when the
    rows validated last time are unchanged, the row checks are skipped. A decoder error clears
    ``held``'s retained rows.
    """
    try:
        return _decode(buf, held)
    except WireError:
        if held is not None:
            held._rows_ok = None
        raise


def _first_nonzero(raw) -> int:
    return bytes(raw).translate(None, _ZERO)[0]


def _decode(buf, held: Dictionary | None) -> Block:  # noqa: C901 - one pass per WIRE.md 3.10 step
    mv = memoryview(buf)
    if mv.format != "B" or mv.ndim != 1:
        mv = mv.cast("B")
    length, code, flags, corr = parse_frame_header(mv)
    total = len(mv)
    if code != 5:
        raise _err("not-a-block", frame_type=code)
    if total < length:
        raise _err("truncated", context="frame", need=length, have=total)
    if total > length:
        raise _err("trailing-bytes", extra=total - length)
    if not flags & 1:
        raise _err("missing-section", section="header")
    have = total - FRAME_HEADER_LEN
    if have < 2:
        raise _err("truncated", context="block directory", need=2, have=have)
    if have < 4:
        raise _err("truncated", context="block directory", need=4, have=have)
    n_sec, dir_reserved = _DIR_HDR.unpack_from(mv, FRAME_HEADER_LEN)
    if dir_reserved:
        raise _err("section-reserved", value=dir_reserved)
    dir_bytes = 8 * n_sec
    if total - 16 < dir_bytes:
        raise _err("truncated", context="block directory", need=dir_bytes, have=total - 16)
    flat = _dir_struct(n_sec).unpack_from(mv, 16)
    kinds = flat[0::4]
    versions = flat[1::4]
    sflags = flat[2::4]
    lens = flat[3::4]
    for kind, body_len in zip(kinds, lens):
        if body_len & 7:
            raise _err("section-length", section=kind, declared=body_len, expected=_align8(body_len))
    last = -1
    for index, kind in enumerate(kinds):
        rank = _RANK.get(kind)
        if rank is None:
            continue
        if rank < last:
            raise _err("section-order", index=index, kind_code=kind)
        last = rank
    if kinds and kinds[0] not in _RANK:
        raise _err("section-order", index=0, kind_code=kinds[0])
    offset = 16 + dir_bytes
    starts = []
    for body_len in lens:
        end = offset + body_len
        if end > total:
            raise _err("truncated", context="section body", need=end, have=total)
        starts.append(offset)
        offset = end
    if offset != total:
        raise _err("trailing-bytes", extra=total - offset)

    header = None
    skipped = []
    for index in range(n_sec):
        kind = kinds[index]
        sf = sflags[index]
        if sf & ~1:
            raise _err("unknown-section-flags", kind_code=kind, flags=sf)
        if kind not in _RANK:
            if sf & 1:
                raise _err("unsupported-required-section", kind_code=kind)
            skipped.append(kind)
            continue
        if versions[index] != 1:
            raise _err("unsupported-section-version", kind_code=kind, version=versions[index], supported=1)
        if (kind == 1 or kind == 6) and not sf & 1:
            raise _err("unknown-section-flags", kind_code=kind, flags=sf)
        if kind == 1 and header is None:
            if lens[index] != HEADER_BODY_LEN:
                raise _err("section-length", section="header", declared=lens[index], expected=HEADER_BODY_LEN)
            values = _HEADER_BODY.unpack_from(mv, starts[index])
            if values[10]:
                raise _err("unknown-section-flags", kind_code=1, flags=values[10])
            header = BlockHeader._make(values)
    if header is None:
        raise _err("missing-section", section="header")

    n_rows = header.n_rows
    seen = 0
    cols: dict[int, tuple[int, int, int]] = {}
    rows_at = time_at = subject = carried = None
    for index in range(n_sec):
        kind = kinds[index]
        if kind not in _RANK:
            continue
        if kind in _ONE_OF:
            bit = 1 << kind
            if seen & bit:
                raise _err("duplicate-section", kind_code=kind)
            seen |= bit
        if kind == 1:
            continue
        at = starts[index]
        body_len = lens[index]
        if kind == 3:
            expected = _align8(8 + 9 * n_rows)
            if body_len != expected:
                raise _err("section-length", section="columns", declared=body_len, expected=expected)
            metric, type_code, kind_code, reserved = _COL_HDR.unpack_from(mv, at)
            if type_code - 1 not in (0, 1, 2):
                raise _err("unknown-value-type", code=type_code)
            if kind_code > 2:
                raise _err("unknown-column-kind", code=kind_code)
            if reserved:
                raise _err("column-reserved", metric=metric, pad=reserved)
            q_at = at + 8 + 8 * n_rows
            if bytes(mv[q_at : q_at + n_rows]).translate(None, _VALID_QUALITY):
                row, code_ = next((r, c) for r, c in enumerate(mv[q_at : q_at + n_rows]) if c > 7)
                raise _err("unknown-quality", row=row, code=code_)
            if bytes(mv[q_at + n_rows : at + body_len]).translate(None, _ZERO):
                raise _err("non-zero-reserved", value=_first_nonzero(mv[q_at + n_rows : at + body_len]))
            if metric in cols:
                raise _err("conflicting-column", metric=metric)
            cols[metric] = (type_code, kind_code, at)
        elif kind == 2:
            expected = 8 * n_rows
            if body_len != expected:
                raise _err("section-length", section="rows", declared=body_len, expected=expected)
            rows_at = at
        elif kind == 4:
            expected = _align8(15 * n_rows)
            if body_len != expected:
                raise _err("section-length", section="time", declared=body_len, expected=expected)
            bad = bytes(mv[at + 14 * n_rows : at + 15 * n_rows]).translate(None, _VALID_AGG)
            if bad:
                raise _err("unknown-aggregation", code=bad[0])
            if bytes(mv[at + 15 * n_rows : at + body_len]).translate(None, _ZERO):
                raise _err("non-zero-reserved", value=_first_nonzero(mv[at + 15 * n_rows : at + body_len]))
            time_at = at
        elif kind == 5:
            body = mv[at : at + body_len]
            if held is not None and held._raw is not None and body == held._raw:
                carried = held
            else:
                carried = Dictionary.decode(body)
        else:
            if body_len != 8:
                raise _err("section-length", section="subject", declared=body_len, expected=8)
            subject, reserved = _U32x2.unpack_from(mv, at)
            if reserved:
                raise _err("unknown-section-flags", kind_code=6, flags=reserved)
    if rows_at is not None and time_at is not None:
        raise _err("rows-and-time", rows=n_rows, buckets=n_rows)
    if n_rows > 0 and rows_at is None and time_at is None:
        raise _err("no-rows-or-time", n_rows=n_rows)
    if subject is not None and time_at is None:
        raise _err("subject-without-time", subject=subject)

    dictionary = carried if carried is not None else held
    retain = None
    if dictionary is not None:
        if dictionary.generation != header.dict_gen:
            raise _err("stale-dictionary-generation", needed=header.dict_gen, held=dictionary.generation)
        dict_len = len(dictionary)
        dkinds = dictionary.kinds
        if subject is not None:
            if subject >= dict_len:
                raise _err("dangling-dictionary-ref", index=subject, entries=dict_len)
            if dkinds[subject]:
                raise _err("row-reference-not-entity", index=subject)
        if rows_at is not None:
            ents_mv = mv[rows_at : rows_at + 4 * n_rows]
            previous = dictionary._rows_ok
            if previous is None or ents_mv != previous:
                ents = _view(ents_mv, "I")
                if n_rows and (max(ents) >= dict_len or any(map(dkinds.__getitem__, ents))):
                    for ent in ents:
                        if ent >= dict_len:
                            raise _err("dangling-dictionary-ref", index=ent, entries=dict_len)
                        if dkinds[ent]:
                            raise _err("row-reference-not-entity", index=ent)
            if total <= RETAIN_LIMIT:
                retain = ents_mv
        mkinds = dictionary.metric_kinds
        for metric, (_t, kind_code, _at) in cols.items():
            if metric >= dict_len:
                raise _err("dangling-dictionary-ref", index=metric, entries=dict_len)
            if dkinds[metric] != METRIC:
                raise _err("column-reference-not-metric", index=metric)
            if mkinds[metric] != kind_code:
                raise _err(
                    "column-kind-mismatch", index=metric, declared=kind_code, dictionary=mkinds[metric]
                )
    if time_at is not None and cols and n_rows:
        aggs = bytes(mv[time_at + 14 * n_rows : time_at + 15 * n_rows])
        if any(aggs.translate(None, _ALLOWED_AGG[k]) for k in {c[1] for c in cols.values()}):
            for agg in aggs:
                for metric, (_t, kind_code, _at) in cols.items():
                    if agg not in _ALLOWED_AGG[kind_code]:
                        raise _err("aggregation-not-permitted", metric=metric, agg=agg)
    if dictionary is not None:
        dictionary._rows_ok = retain

    block = Block()
    block.corr = corr
    block.header = header
    block.dictionary = carried
    block.dictionary_in_force = dictionary
    block.subject = subject
    block.skipped = skipped
    block.directory_kinds = kinds
    block.buf = buf
    block._mv = mv
    block._rows_at = rows_at
    block._time_at = time_at
    block._cols = cols
    return block
