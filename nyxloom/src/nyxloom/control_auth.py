"""Operator authentication for the HTTP control plane (CR-15 / RISK-005).

The credential is instance state, not project configuration.  Starting the
HTTP server creates ``daemon/control-credential.json`` with mode 0600 when it
is absent; operators retrieve or rotate it explicitly with ``nyxloom auth``.
The file is read for every mutation, so an atomic rotation invalidates the old
credential immediately -- no daemon restart, no in-memory cache to expire.

Secrets are deliberately absent from this module's log and event interfaces.
The only value callers receive after authentication is a named ``Actor``.

TRUST BOUNDARY -- what this module does and does not claim (the review
amendment's rule: an assertion about the trust boundary is testable or
absent).  Each claim below has a test in ``tests/test_control_auth.py``.

- ``load`` accepts the store only as a regular file, mode exactly 0600, owned
  by the current euid, of bounded size and exact schema.  Every other outcome
  raises ``CredentialStoreError``; there is no fallback to a previous value.
  A caller that cannot read the trust root must refuse the mutation.
- Because ownership is checked, a group- or world-writable *parent* directory
  is not by itself an escalation: another user can create a file or symlink
  there, but not one this loader will accept.  The parent's mode is therefore
  deliberately NOT checked -- refusing on it would fail closed on deployments
  whose state root is a shared mount, buying nothing.
- Anyone who can read the store, or who runs as the daemon's uid, holds full
  control authority.  This is authentication, not privilege separation.
- There is no rate limit here.  The credential is 256 bits of
  ``secrets.token_urlsafe`` entropy, so online guessing is not the exposure
  that motivated the package; each attempt is audited instead.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .types import Actor, ActorKind


CREDENTIAL_FILENAME = "control-credential.json"
SCHEMA_VERSION = 1
_MAX_STORE_BYTES = 16 * 1024
_OPERATOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}")

# One instance-global ledger for control-plane audit events (refusals,
# rotations, daemon-scoped config changes).  A synthetic project id keeps
# exactly-one-event semantics for operations that affect every registered
# project -- or none of them, as an unauthenticated refusal does, since the
# target is never parsed.  Readable with `nyxloom events _nyxloom-control`.
# The leading underscore cannot collide with a real project id: `nyxloom
# register` resolves ids from a project's own [project].id, and the dashboard
# only ever renders registered projects, so nothing enumerates this ledger as
# a project (verified by test_control_ledger_is_not_a_registered_project).
CONTROL_LEDGER_PROJECT = "_nyxloom-control"

# Actor id for a request that failed authentication.  It begins with "-", so
# `_OPERATOR_RE` can never accept it as a real operator identity: a refusal
# event is therefore unforgeable by naming yourself "unauthenticated".
UNAUTHENTICATED_ACTOR_ID = "-unauthenticated"


class CredentialStoreError(RuntimeError):
    """The credential store cannot be trusted; mutations must fail closed."""


def unauthenticated_actor() -> Actor:
    """The audit actor for a refused mutation -- never a resolvable operator."""
    return Actor(ActorKind.OPERATOR, UNAUTHENTICATED_ACTOR_ID)


class HeaderValues(Protocol):
    def get_all(self, name: str) -> list[str] | None: ...


@dataclass(frozen=True)
class OperatorCredential:
    operator_id: str
    credential: str
    generation: int

    @property
    def actor(self) -> Actor:
        return Actor(ActorKind.OPERATOR, self.operator_id)


def default_operator_id() -> str:
    """Return the explicit bootstrap identity, or a stable local-user name."""
    candidate = os.environ.get("NYXLOOM_OPERATOR_ID", "").strip()
    if not candidate:
        try:
            candidate = getpass.getuser().strip()
        except (ImportError, KeyError, OSError):
            candidate = ""
    if not _OPERATOR_RE.fullmatch(candidate):
        candidate = "local-operator"
    return candidate


class CredentialStore:
    """Atomic 0600 credential store rooted in the daemon state directory."""

    def __init__(self, daemon_state_dir: Path):
        self.path = daemon_state_dir / CREDENTIAL_FILENAME

    def ensure(self, operator_id: str | None = None) -> OperatorCredential:
        """Load an existing store or create the first credential atomically.

        Never repairs or replaces a store `load` refuses: an existing file is
        returned through `load` (raising on any trust failure), so a boot can
        neither overwrite a credential the operator still holds nor quietly
        mint a new one because the old file looked wrong.
        """
        if self.path.exists() or self.path.is_symlink():
            return self.load()
        record = OperatorCredential(
            # `is None`, not falsiness: an explicitly EMPTY --operator is a
            # usage error, not a request for the default identity.
            operator_id=self._validate_operator(
                default_operator_id() if operator_id is None else operator_id),
            credential=secrets.token_urlsafe(32),
            generation=1,
        )
        data = self._encode(record)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL, not tmp+rename: bootstrap must never clobber a store
            # another process created a moment ago.
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return self.load()
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                # A half-written store is worse than none: it would fail the
                # loader's checks forever and could not be told apart from
                # tampering. Remove it and report failing closed.
                self.path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise CredentialStoreError(
                f"operator credential could not be created: {exc.strerror or exc}") from exc
        return record

    def load(self) -> OperatorCredential:
        """Read and strictly validate the current credential.

        Permissions, ownership, links, malformed JSON, and partial writes are
        trust-boundary failures, not reasons to fall back to an old value.
        """
        try:
            info = self.path.lstat()
        except OSError as exc:
            raise CredentialStoreError("operator credential is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise CredentialStoreError("operator credential is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise CredentialStoreError("operator credential permissions must be 0600")
        if info.st_uid != os.geteuid():
            raise CredentialStoreError("operator credential has an unexpected owner")
        if info.st_size <= 0 or info.st_size > _MAX_STORE_BYTES:
            raise CredentialStoreError("operator credential has an invalid size")
        try:
            raw = self.path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialStoreError("operator credential is unreadable") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "operator_id", "credential", "generation"
        }:
            raise CredentialStoreError("operator credential has an invalid shape")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise CredentialStoreError("operator credential schema is unsupported")
        operator_id = self._validate_operator(value.get("operator_id"))
        credential = value.get("credential")
        generation = value.get("generation")
        if not isinstance(credential, str) or not 32 <= len(credential) <= 512:
            raise CredentialStoreError("operator credential value is invalid")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise CredentialStoreError("operator credential generation is invalid")
        return OperatorCredential(operator_id, credential, generation)

    def authenticate(self, headers: HeaderValues | Mapping[str, str]) -> Actor | None:
        """Return the credential's named actor, or None for missing/invalid auth.

        Store failures deliberately propagate as ``CredentialStoreError`` so
        callers can distinguish an unavailable trust root from bad credentials
        while still refusing the mutation.
        """
        record = self.load()
        values: list[str]
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            values = list(get_all("Authorization") or [])
        else:
            value = headers.get("Authorization")
            values = [value] if isinstance(value, str) else []
        if len(values) != 1:
            return None
        parts = values[0].strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            return None
        supplied_digest = hashlib.sha256(parts[1].encode("utf-8")).digest()
        expected_digest = hashlib.sha256(record.credential.encode("utf-8")).digest()
        return record.actor if hmac.compare_digest(supplied_digest, expected_digest) else None

    def rotate(self, operator_id: str | None = None, *,
               force: bool = False) -> OperatorCredential:
        """Atomically replace the credential and return the new secret once.

        ``force`` is the recovery path for a store this loader refuses (bad
        mode, foreign owner, truncated write): rotation normally needs the
        current record to carry the identity and generation forward, which
        would otherwise leave a fail-closed daemon with no way back except
        deleting the file by hand.  A forced rotation restarts the generation
        counter at 1 and adopts ``default_operator_id()`` unless an identity is
        passed, since neither can be read from the file it replaces.
        """
        try:
            current = self.load()
        except CredentialStoreError:
            if not force:
                raise
            current = None
        if operator_id is None:
            # A forced rotation cannot read the identity to carry forward, so
            # it falls back to the local default -- pass --operator to keep a
            # specific name (asserted in test_forced_rotation_*).
            operator_id = current.operator_id if current else default_operator_id()
        record = OperatorCredential(
            operator_id=self._validate_operator(operator_id),
            credential=secrets.token_urlsafe(32),
            generation=current.generation + 1 if current else 1,
        )
        temp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(self._encode(record))
                    stream.flush()
                    os.fsync(stream.fileno())
                # Atomic swap: a concurrent reader sees either the whole old
                # record or the whole new one, never a partial file -- and the
                # 0600 temp mode travels with the rename, so a widened mode is
                # repaired by rotating. fsync the directory so the swap itself
                # survives a crash, not just the bytes.
                os.replace(temp, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                temp.unlink(missing_ok=True)
                raise
        except OSError as exc:
            # e.g. a read-only state dir, or a directory sitting where the
            # store belongs. Fail closed through this module's own error type
            # so no caller has to know about errno.
            raise CredentialStoreError(
                f"operator credential could not be rotated: {exc.strerror or exc}") from exc
        return record

    @staticmethod
    def _validate_operator(value: object) -> str:
        if not isinstance(value, str) or not _OPERATOR_RE.fullmatch(value):
            raise CredentialStoreError(
                "operator identity must match [A-Za-z0-9][A-Za-z0-9._@-]{0,127}"
            )
        return value

    @staticmethod
    def _encode(record: OperatorCredential) -> bytes:
        return (json.dumps({
            "schema_version": SCHEMA_VERSION,
            "operator_id": record.operator_id,
            "credential": record.credential,
            "generation": record.generation,
        }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def authorization_header(record: OperatorCredential) -> dict[str, str]:
    """Explicit client helper; callers must never log the returned mapping."""
    return {"Authorization": f"Bearer {record.credential}"}
