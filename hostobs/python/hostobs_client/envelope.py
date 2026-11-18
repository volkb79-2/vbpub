"""JSON envelope checks of REQ, REP, END and EVT payloads (WIRE.md 2.2, 4.1, 4.2, 8.2).

Standard library only. The shape rules are those of ``crates/proto/src/envelope.rs`` (one object,
nothing before the ``{`` or after the ``}``, nesting depth at most 8 with the envelope object at
depth 1, the envelope's own key set compared **as written** so an escaped spelling of a key is an
unknown key, the ``ok`` literal, exact ``u32`` counts, ``closing`` carrying exactly ``event`` and
``reason``). The JSON text itself is parsed with the standard ``json`` module on the validated
bytes, and the rules that module does not enforce are enforced here: no duplicate key at any depth,
no ``NaN``/``Infinity``, no non-finite float, no ``-0`` integer, the depth bound (a C-speed bracket
count over the payload with its strings removed, before any parsing).

A violation of a REP, END or EVT is a local :class:`EnvelopeError` (a :class:`WireError`: the server
is the faulty peer and the connection is closed, WIRE.md 6.5).
"""

from __future__ import annotations

import json
import re
from itertools import accumulate
from typing import Any, NamedTuple

from .wire import WireError

MAX_DEPTH = 8
MAX_MEMBERS = 8
U32_MAX = 0xFFFFFFFF


class LocalError(WireError):
    """A refusal made by the client itself (not one of the 46 decoder kinds of WIRE.md 3.10)."""

    KINDS: frozenset = frozenset()

    def __init__(self, kind: str, detail: dict | None = None) -> None:
        if kind not in self.KINDS:
            raise ValueError(f"{kind!r} is not a {type(self).__name__} kind")
        Exception.__init__(self, kind)
        self.kind = kind
        self.detail = detail if detail is not None else {}


class EnvelopeError(LocalError):
    """The payload is not a valid envelope; ``kind`` mirrors ``EnvelopeError::kind`` in Rust."""

    KINDS = frozenset(("json-text", "duplicate-key", "unknown-key", "missing-key", "wrong-type"))


def _fail(kind: str, **detail: object) -> EnvelopeError:
    return EnvelopeError(kind, detail)


# ------------------------------------------------------------------------------ text rules

_STRING_RE = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"', re.DOTALL)
_KEY_RE = re.compile(r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"')
_WS_RE = re.compile(r"[ \t\n\r]*")
_BRACKETS = b"[]{}"
_DROP = bytes(byte for byte in range(256) if byte not in _BRACKETS)
_DELTA = tuple((1 if chr(byte) in "[{" else -1 if chr(byte) in "]}" else 0) for byte in range(256))


def max_depth(raw: bytes) -> int:
    """The deepest nesting of ``raw`` counting every ``{``/``[`` outside strings (C-speed passes)."""
    stripped = _STRING_RE.sub(b"", raw).translate(None, _DROP)
    return max(accumulate(map(_DELTA.__getitem__, stripped)), default=0)


class _Duplicate(ValueError):
    pass


def _pairs(pairs: list) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _Duplicate("duplicate key")
    return result


def _parse_int(text: str) -> int:
    if text == "-0":
        raise ValueError("negative zero")
    return int(text)


def _parse_float(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("non-finite number")
    return value


def _parse_constant(text: str) -> Any:
    raise ValueError(f"{text} is not JSON")


_DECODER = json.JSONDecoder(
    object_pairs_hook=_pairs, parse_int=_parse_int, parse_float=_parse_float, parse_constant=_parse_constant
)


def _text_of(data: Any) -> tuple[bytes, str]:
    """The payload as bytes and strict UTF-8 text; ``{`` first and ``}`` last, depth within bound."""
    raw = bytes(data)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _fail("json-text", reason="not UTF-8") from error
    if raw[:1] != b"{" or raw[-1:] != b"}":
        raise _fail("json-text", reason="not exactly one object")
    if max_depth(raw) > MAX_DEPTH:
        raise _fail("json-text", reason="nesting deeper than 8")
    return raw, text


def _value(text: str, start: int) -> tuple[Any, int]:
    try:
        return _DECODER.raw_decode(text, start)
    except _Duplicate as error:
        raise _fail("duplicate-key", reason="a key repeats inside a value") from error
    except (ValueError, RecursionError) as error:
        raise _fail("json-text", reason=str(error)[:80]) from error


class Member(NamedTuple):
    key: str  # the key as written between its quotes (escapes not decoded)
    value: Any
    start: int
    end: int


def _members(text: str, start: int, end: int) -> list[Member]:
    """The members of the object occupying exactly ``text[start:end]``; keys are kept as written."""
    if text[start : start + 1] != "{":
        raise _fail("json-text", reason="not an object")
    at = _WS_RE.match(text, start + 1).end()
    found: list[Member] = []
    if text[at : at + 1] == "}":
        at += 1
    else:
        while True:
            match = _KEY_RE.match(text, at)
            if match is None:
                raise _fail("json-text", reason="expected a key")
            key = match.group()[1:-1]
            at = _WS_RE.match(text, match.end()).end()
            if text[at : at + 1] != ":":
                raise _fail("json-text", reason="expected a colon")
            at = _WS_RE.match(text, at + 1).end()
            value, after = _value(text, at)
            if any(seen.key == key for seen in found):
                raise _fail("duplicate-key", key=key)
            if len(found) >= MAX_MEMBERS:
                raise _fail("unknown-key", key=key)
            found.append(Member(key, value, at, after))
            at = _WS_RE.match(text, after).end()
            nxt = text[at : at + 1]
            at += 1
            if nxt == ",":
                at = _WS_RE.match(text, at).end()
            elif nxt == "}":
                break
            else:
                raise _fail("json-text", reason="expected , or }")
    if at != end:
        raise _fail("json-text", reason="bytes after the object")
    return found


def members(data: Any) -> tuple[str, list[Member]]:
    """The text and top-level members of ``data`` (one JSON object, WIRE.md 4.2)."""
    _, text = _text_of(data)
    return text, _members(text, 0, len(text))


def parse_object(data: Any) -> dict:
    """The decoded JSON object of ``data`` under every text rule of WIRE.md 4.2 (any depth)."""
    _, text = _text_of(data)
    value, end = _value(text, 0)
    if end != len(text) or not isinstance(value, dict):
        raise _fail("json-text", reason="not exactly one object")
    return value


# ------------------------------------------------------------------------------ shapes

_STR, _OBJ, _NUM, _BOOL, _STR_OR_NULL = "str", "object", "number", "bool", "str|null"


def _kind_ok(value: Any, want: str) -> bool:
    if want == _STR:
        return type(value) is str
    if want == _OBJ:
        return type(value) is dict
    if want == _NUM:
        return type(value) in (int, float)
    if want == _BOOL:
        return type(value) is bool
    return value is None or type(value) is str


def _only(found: list[Member], allowed: tuple[str, ...]) -> None:
    for member in found:
        if member.key not in allowed:
            raise _fail("unknown-key", key=member.key)


def _field(found: list[Member], label: str, want: str) -> Any:
    for member in found:
        if member.key == label:
            if not _kind_ok(member.value, want):
                raise _fail("wrong-type", key=label)
            return member.value
    raise _fail("missing-key", key=label)


def _u32(found: list[Member], label: str) -> int:
    _field(found, label, _NUM)
    value = next(member.value for member in found if member.key == label)
    if type(value) is not int or not 0 <= value <= U32_MAX:
        raise _fail("wrong-type", key=label)
    return value


def _check_error_object(text: str, found: list[Member]) -> dict:
    inner = next(member for member in found if member.key == "error")
    parts = _members(text, inner.start, inner.end)
    _only(parts, ("code", "message", "detail"))
    _field(parts, "code", _STR)
    _field(parts, "message", _STR)
    _field(parts, "detail", _OBJ)
    return inner.value


class Rep(NamedTuple):
    ok: bool
    body: dict  # ``result`` when ok, else the error object {"code", "message", "detail"}
    corr: int | None  # JSON mode only


def _corr(found: list[Member], json_mode: bool) -> int | None:
    return _u32(found, "corr") if json_mode else None


def check_rep(data: Any, json_mode: bool = False) -> Rep:
    """Check a REP payload (WIRE.md 4.1); JSON mode adds ``corr`` (a REP may carry corr 0)."""
    text, found = members(data)
    ok = _field(found, "ok", _BOOL)
    label = "result" if ok else "error"
    _only(found, ("ok", label) + (("corr",) if json_mode else ()))
    body = _field(found, label, _OBJ)
    if not ok:
        _check_error_object(text, found)
    return Rep(ok, body, _corr(found, json_mode))


class End(NamedTuple):
    ok: bool
    blocks: int
    dropped: int
    reason: str | None
    next: str | None
    error: dict | None


def check_end(data: Any) -> End:
    """Check an END payload (WIRE.md 2.2)."""
    text, found = members(data)
    ok = _field(found, "ok", _BOOL)
    _only(found, ("ok", "blocks", "dropped", "next", "reason") if ok else ("ok", "blocks", "dropped", "error"))
    blocks = _u32(found, "blocks")
    dropped = _u32(found, "dropped")
    if not ok:
        _field(found, "error", _OBJ)
        return End(False, blocks, dropped, None, None, _check_error_object(text, found))
    cursor = _field(found, "next", _STR_OR_NULL)
    reason = _field(found, "reason", _STR)
    return End(True, blocks, dropped, reason, cursor, None)


def check_evt(data: Any) -> dict:
    """Check an EVT payload: a string ``event``; ``closing`` carries exactly ``event`` and ``reason``."""
    _, found = members(data)
    event = _field(found, "event", _STR)
    if event == "closing":
        _only(found, ("event", "reason"))
        _field(found, "reason", _STR)
    return {member.key: member.value for member in found}


def build_request(verb: str, args: dict, corr: int | None = None) -> bytes:
    """The REQ payload ``{"verb", "args"}`` (JSON mode adds ``corr``) as UTF-8, checked locally.

    Raises ``ValueError`` for what the server would refuse as ``bad-request``: a non-string verb or
    one with a backslash, ``args`` that is not an object, non-finite floats, a lone surrogate, or
    nesting deeper than 8.
    """
    if not isinstance(verb, str) or not verb:
        raise ValueError("verb must be a non-empty string")
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    envelope: dict = {"verb": verb, "args": args}
    if corr is not None:
        envelope = {"corr": corr, **envelope}
    try:
        raw = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as error:
        raise ValueError(f"request is not encodable as JSON: {error}") from error
    if b"\\" in json.dumps(verb, ensure_ascii=False).encode("utf-8")[1:-1]:
        raise ValueError("verb must not contain a backslash or a quote")
    if max_depth(raw) > MAX_DEPTH:
        raise ValueError("request nests deeper than 8")
    return raw
