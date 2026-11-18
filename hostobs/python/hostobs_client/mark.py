"""``hostobs-mark``: write one ``annotate`` line to the observer socket (WIRE.md 8.5, 4.12).

Standard library only, JSON mode (no hello, no binary frames): connect to
``HOSTOBS_OBSERVER_SOCKET``, send one request line, ``shutdown(SHUT_WR)``, read one reply line.
Exit 0 on success. When hostobs is unavailable (variable unset, socket unreachable, error reply,
bad reply) it prints one notice to stderr and exits 0, unless ``--require``, which exits 1.
Usage errors exit 2. ``ctx`` comes from ``HOSTOBS_CTX_{RUN,LANE,CANDIDATE,TXN,SESSION}``.

    python3 -m hostobs_client.mark begin|end|point NAME [--key K] [--outcome O] [--code C]
                                   [--attr K=V ...] [--require]
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
from typing import Any, Callable, Mapping, Sequence, TextIO

from . import envelope
from .conn import check_timeout
from .wire import HostobsError, ServerError

SOCKET_VARIABLE = "HOSTOBS_OBSERVER_SOCKET"
CTX_KEYS = ("run", "lane", "candidate", "txn", "session")
MAX_REPLY_LINE = 64 * 1024
_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_NS = 10**9


class MarkError(HostobsError):
    """The mark could not be written or the reply was not an acknowledgement."""


def context_from_environment(environ: Mapping[str, str]) -> dict:
    """``{"run": ..., ...}`` from the ``HOSTOBS_CTX_*`` variables that are set and non-empty."""
    ctx = {}
    for key in CTX_KEYS:
        value = environ.get(f"HOSTOBS_CTX_{key.upper()}")
        if value:
            ctx[key] = value
    return ctx


def build_args(
    kind: str,
    name: str,
    *,
    key: str | None = None,
    outcome: str | None = None,
    code: str | None = None,
    attrs: Mapping[str, str] | None = None,
    ctx: Mapping[str, str] | None = None,
) -> dict:
    """The ``annotate`` args of WIRE.md 4.12 (every key present), validated locally."""
    if kind not in ("begin", "end", "point"):
        raise ValueError("kind must be begin, end or point")
    if not _NAME.fullmatch(name):
        raise ValueError("name must match ^[a-z][a-z0-9_.-]{0,63}$")
    if outcome is not None and kind != "end":
        raise ValueError("outcome is only valid for end")
    attrs = dict(attrs or {})
    if len(attrs) > 8 or any(not _NAME.fullmatch(k) or len(v.encode("utf-8")) > 256 for k, v in attrs.items()):
        raise ValueError("attrs: at most 8 pairs, key a name, value at most 256 bytes")
    return {
        "kind": kind,
        "name": name,
        "key": key,
        "ctx": dict(ctx or {}),
        "outcome": outcome,
        "code": code,
        "attrs": attrs,
    }


def send_mark(
    path: str,
    args: dict,
    *,
    timeout_s: float = 5.0,
    clock: Callable[[], int] = time.monotonic_ns,
) -> dict:
    """Send one ``annotate`` line and return its ``result``; failures raise :class:`MarkError`/`ServerError`."""
    check_timeout(timeout_s)
    request = envelope.build_request("annotate", args, corr=1) + b"\n"
    deadline = clock() + int(timeout_s * _NS)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout_s)
        sock.connect(path)
        sock.settimeout(max(0.001, (deadline - clock()) / _NS))
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        line = bytearray()
        while True:
            if clock() > deadline:
                raise MarkError("timed out waiting for the reply")
            sock.settimeout(max(0.001, (deadline - clock()) / _NS))
            chunk = sock.recv(4096)
            if not chunk:
                raise MarkError("connection closed before a reply line")
            line += chunk
            if b"\n" in chunk:
                break
            if len(line) > MAX_REPLY_LINE:
                raise MarkError("reply line too long")
    except TimeoutError as error:
        raise MarkError("timed out") from error
    except OSError as error:
        raise MarkError(f"{error.strerror or type(error).__name__} (errno {error.errno})") from error
    finally:
        sock.close()
    text = bytes(line).split(b"\n", 1)[0]
    try:
        rep = envelope.check_rep(text, json_mode=True)
    except envelope.EnvelopeError as error:
        raise MarkError(f"malformed reply: {error}") from error
    if rep.corr not in (0, 1):
        raise MarkError(f"reply for corr {rep.corr}")
    if not rep.ok:
        error_body = rep.body
        raise ServerError(error_body["code"], error_body["message"], error_body["detail"])
    return rep.body


def mark(kind: str, name: str, *, environ: Mapping[str, str] | None = None, **options: Any) -> dict:
    """Annotate through the socket named by ``HOSTOBS_OBSERVER_SOCKET``; raises :class:`MarkError` if unset."""
    env = os.environ if environ is None else environ
    send_options = {k: options.pop(k) for k in ("timeout_s", "clock") if k in options}
    options.setdefault("ctx", context_from_environment(env))
    args = build_args(kind, name, **options)  # a usage error (ValueError) is judged before the socket
    path = env.get(SOCKET_VARIABLE)
    if not path:
        raise MarkError(f"{SOCKET_VARIABLE} is unset")
    return send_mark(path, args, **send_options)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hostobs-mark", description="Write one hostobs annotation.")
    parser.add_argument("kind", choices=("begin", "end", "point"))
    parser.add_argument("name")
    parser.add_argument("--key")
    parser.add_argument("--outcome")
    parser.add_argument("--code")
    parser.add_argument("--attr", action="append", default=[], metavar="K=V")
    parser.add_argument("--require", action="store_true", help="exit 1 when the mark could not be written")
    parser.add_argument("--timeout", type=_timeout_argument, default=5.0, metavar="SECONDS")
    return parser


def _timeout_argument(text: str) -> float:
    """A finite positive number of seconds (``inf``, ``nan`` and ``1e400`` are usage errors, exit 2)."""
    try:
        value = float(text)
        check_timeout(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a finite positive number of seconds") from error
    return value


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stderr: TextIO | None = None,
    clock: Callable[[], int] = time.monotonic_ns,
) -> int:
    err = sys.stderr if stderr is None else stderr
    args = _parser().parse_args(argv)
    attrs = {}
    for pair in args.attr:
        key, sep, value = pair.partition("=")
        if not sep:
            _parser().error(f"--attr needs K=V, got {pair!r}")
        attrs[key] = value
    try:
        mark(
            args.kind,
            args.name,
            environ=environ,
            key=args.key,
            outcome=args.outcome,
            code=args.code,
            attrs=attrs,
            timeout_s=args.timeout,
            clock=clock,
        )
    except ValueError as error:
        print(f"hostobs-mark: {error}", file=err)
        return 2
    except HostobsError as error:
        print(f"hostobs-mark: mark not written: {error}", file=err)
        return 1 if args.require else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
