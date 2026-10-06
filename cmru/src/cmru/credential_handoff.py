"""Parent-to-child publisher credential handoff for release transactions.

``cmru release`` resolves publisher credentials on the host, from the caller's
checkout (the root ``cmru.secret.toml``, a project overlay, or the invocation
environment token, which wins).  The transaction child runs from an isolated
release worktree and must never find a credential FILE there: the parent hands
the already-resolved values over a private inherited pipe instead.

Properties this module guarantees:

* the payload travels only on an inherited pipe descriptor -- never argv, never
  a file, never the child's environment (only the descriptor NUMBER is exported);
* the child consumes the pipe at most once and caches the result in-process;
* a transaction child that received no handoff fails closed rather than
  falling back to reading ``cmru.secret.toml`` from its own root;
* a process nested below a consuming child (a project step that re-invokes
  cmru) inherits the "consumed" marker and sees only the environment token.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
from dataclasses import dataclass, field
from typing import Mapping

#: Set to ``"1"`` by the release launcher for its managed child worktree process.
CHILD_ENV = "CMRU_RELEASE_TRANSACTION_CHILD"
#: Carries the inherited read-end descriptor NUMBER (never the credential).
CREDENTIAL_FD_ENV = "CMRU_INTERNAL_CREDENTIAL_FD"
#: Exported by a child after it consumed its handoff, for nested invocations.
CREDENTIAL_STATE_ENV = "CMRU_INTERNAL_CREDENTIAL_STATE"
_CONSUMED = "consumed"
_SCHEMA_VERSION = 1
# Far below the default 64 KiB pipe capacity, so the parent's write never blocks.
MAX_PAYLOAD_BYTES = 32 * 1024


@dataclass(frozen=True)
class CredentialHandoff:
    """The credentials a parent resolved: a root token and per-project tokens."""

    root: str = ""
    projects: Mapping[str, str] = field(default_factory=dict)
    #: True for a process nested below a consuming child (no pipe, by design).
    nested: bool = False


_CACHE: CredentialHandoff | None = None


def reset_cache() -> None:
    """Forget the in-process handoff (tests and long-lived embedders only)."""
    global _CACHE
    _CACHE = None


def encode(handoff: CredentialHandoff) -> bytes:
    """Serialize *handoff* for the pipe; refuse payloads that could block a write."""
    payload = json.dumps(
        {
            "schema_version": _SCHEMA_VERSION,
            "root": handoff.root,
            "projects": dict(handoff.projects),
        },
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise RuntimeError("credential handoff payload is too large")
    return payload


def decode(payload: bytes) -> CredentialHandoff:
    """Parse and strictly validate a pipe payload."""
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid credential handoff payload: {exc}") from exc
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "root", "projects"}
        or raw["schema_version"] != _SCHEMA_VERSION
        or not isinstance(raw["root"], str)
        or not isinstance(raw["projects"], dict)
        or not all(
            isinstance(name, str) and isinstance(token, str)
            for name, token in raw["projects"].items()
        )
    ):
        raise RuntimeError("invalid credential handoff payload: unexpected shape")
    return CredentialHandoff(root=raw["root"], projects=dict(raw["projects"]))


def _read_pipe(raw_fd: str) -> bytes:
    fd = None
    try:
        fd = int(raw_fd)
        if fd <= 2 or not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise ValueError("expected an inherited pipe descriptor")
        os.set_blocking(fd, False)
        payload = bytearray()
        while len(payload) <= MAX_PAYLOAD_BYTES:
            chunk = os.read(fd, MAX_PAYLOAD_BYTES + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload is too large")
        return bytes(payload)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"invalid internal credential pipe: {exc}") from exc
    finally:
        if fd is not None and fd > 2:
            with contextlib.suppress(OSError):
                os.close(fd)


def child_handoff() -> CredentialHandoff | None:
    """Return this process's credentials when it is a transaction child.

    ``None`` means "not a transaction child": ordinary credential resolution
    applies.  Inside a transaction child this NEVER consults a secret file.
    Raises ``RuntimeError`` when a child has neither a handoff nor the consumed
    marker, so a lost handoff cannot silently degrade to a file read.
    """
    global _CACHE
    if os.environ.get(CHILD_ENV) != "1":
        return None
    if _CACHE is not None:
        return _CACHE
    raw_fd = os.environ.pop(CREDENTIAL_FD_ENV, None)
    if raw_fd is not None:
        _CACHE = decode(_read_pipe(raw_fd))
        os.environ[CREDENTIAL_STATE_ENV] = _CONSUMED
        return _CACHE
    if os.environ.get(CREDENTIAL_STATE_ENV) == _CONSUMED:
        # Nested below a consuming child: the pipe is gone by design; only the
        # invocation environment token can apply, and it is read by the caller.
        return CredentialHandoff(nested=True)
    raise RuntimeError(
        "release transaction child received no credential handoff from its "
        "parent; refusing to read cmru.secret.toml from the release worktree"
    )
