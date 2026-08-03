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
  by the current euid, unlinked from any second name, of bounded size and
  exact schema.  Every other outcome raises ``CredentialStoreError``; there is
  no fallback to a previous value.  A caller that cannot read the trust root
  must refuse the mutation.
- Those checks are made on an OPEN FILE DESCRIPTOR, not on a path: ``load``
  opens once with ``O_NOFOLLOW`` and validates with ``fstat`` on that same
  descriptor, so the inode it authenticates against is provably the inode it
  read.  A path-based ``lstat``-then-read leaves a window in which a writer
  of the parent directory can swap a validated file for a symlink or a file
  it controls; there is no such window here.
- Because ownership is checked, a group- or world-writable *parent* directory
  is not by itself an escalation: another user can create a file or symlink
  there, but not one this loader will accept.  The parent's mode is therefore
  deliberately NOT checked -- refusing on it would fail closed on deployments
  whose state root is a shared mount, buying nothing.
- ``rotate`` reports failure ONLY while the old credential is still the live
  one.  Once ``os.replace`` has swapped the file the new value is what
  authenticates, so a later failure (the parent-directory fsync) is reported
  as a durability WARNING and the new credential is still returned -- telling
  the operator "rotation failed" after the old value has already died would
  leave nobody holding a working credential.
- Anyone who can read the store, or who runs as the daemon's uid, holds full
  control authority.  This is authentication, not privilege separation.
- There is no rate limit here.  The credential is 256 bits of
  ``secrets.token_urlsafe`` entropy, so online guessing is not the exposure
  that motivated the package; each attempt is audited instead.
- ``channel_operator`` is NOT authentication and does not claim to be: an ntfy
  topic carries no verifiable sender identity, so the notification-channel
  mutation ingress is CLOSED unless the deployment names the operator its
  write ACL belongs to.  See that function for what the deployment is
  asserting when it sets the variable.
"""

from __future__ import annotations

import email.message
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
from typing import Mapping

from .log import get_logger
from .types import Actor, ActorKind, EventType

log = get_logger("control_auth")

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

# The deployment's assertion about the ntfy feedback topic (see
# `channel_operator`).  Deliberately an environment variable and not a
# nyxloom.toml key: like NYXLOOM_HTTP_BIND it is a property of WHERE this
# daemon runs, and nyxloom.toml is bind-mounted verbatim between host and
# container so it structurally cannot carry a per-deployment value.
CHANNEL_OPERATOR_ENV = "NYXLOOM_CHANNEL_OPERATOR_ID"


class CredentialStoreError(RuntimeError):
    """The credential store cannot be trusted; mutations must fail closed."""


def unauthenticated_actor() -> Actor:
    """The audit actor for a refused mutation -- never a resolvable operator."""
    return Actor(ActorKind.OPERATOR, UNAUTHENTICATED_ACTOR_ID)


def channel_operator() -> Actor | None:
    """The named operator a notification-channel message may act as, or None.

    The ntfy feedback topic is a SECOND mutation ingress into the same
    invariants the HTTP control plane guards: `pause`/`resume` over chat-ops,
    `decide D-NNN <choice>`, and every decision-chat turn.  Nothing in it is
    authenticated:

    - `cmd_token_env` (NTFY_CMD_TOKEN) is the daemon's own READ credential for
      subscribing.  It says nothing about who PUBLISHED a message.
    - ntfy exposes no sender identity at all -- `commands.py` already states
      this, which is why tag-based loop prevention is the only self-echo
      guard available.
    - `Actor(OPERATOR, "ntfy-cmd")` and `Actor(OPERATOR, "feedback-chat")` are
      transport names, exactly the "ui" non-identity CR-15 exists to remove.

    So this process cannot authenticate a channel message, and CR-15 refuses
    to let one mutate state by default.  Setting CHANNEL_OPERATOR_ENV is the
    deployment asserting, out of band, that publish rights on that topic are
    restricted to ONE named human -- and naming them, so their answers are
    attributable.  nyxloom cannot verify that assertion; it can only record
    whether it was made, refuse the ingress when it was not, and put the named
    identity in the resulting events when it was.

    Returns None when unset, empty, or not a usable operator identity; an
    unusable value is refused rather than degraded to a default, because a
    default here would silently re-open the ingress under a fake name.
    """
    candidate = os.environ.get(CHANNEL_OPERATOR_ENV, "").strip()
    if not candidate:
        return None
    if not _OPERATOR_RE.fullmatch(candidate):
        log.warning("channel operator identity is unusable; the notification "
                    "channel's mutating verbs stay closed",
                    variable=CHANNEL_OPERATOR_ENV)
        return None
    return Actor(ActorKind.OPERATOR, candidate)


def channel_operator_for(ingress: str) -> Actor | None:
    """`channel_operator()`, plus the audited refusal a channel ingress owes.

    ONE implementation for both notification-channel ingresses (`commands`'
    verbs and `decision_chat`'s decision routes), so they cannot drift into
    two refusal shapes or two audit payloads.  `ingress` names the route, not
    the target: `ntfy:pause`, `ntfy:decide`.
    """
    operator = channel_operator()
    if operator is None:
        log.warning("channel mutation refused", ingress=ingress,
                    reason="no-named-channel-operator")
        audit_control_refusal(f"ntfy:{ingress}", "no-named-channel-operator")
    return operator


def audit_control_refusal(ingress: str, reason: str) -> None:
    """Append exactly one refusal event to the instance control ledger.

    The payload carries the INGRESS (an HTTP path, or `ntfy:<verb>`) and the
    REASON and nothing else -- no body, no target id, no header value, no
    message text.  Refusals are decided before any target is resolved, so
    there is no target to record: that is what makes a refused reply to a real
    decision id and to a fabricated one indistinguishable in the audit trail
    as well as on the wire.

    Never raises.  A refusal whose audit append fails is still a refusal (the
    caller has already decided to deny), and letting the exception escape
    would replace a constant refusal with an error carrying the storage
    failure -- turning an audit outage into a distinguishable response.  The
    failure itself is logged at ERROR so it cannot pass unnoticed.
    """
    from . import storage           # deferred: storage imports config, which
                                    # must not depend on the trust root
    try:
        storage.append_event(
            CONTROL_LEDGER_PROJECT,
            actor=unauthenticated_actor(),
            type=EventType.CONTROL_MUTATION_REFUSED,
            payload={"path": ingress, "reason": reason},
        )
    except Exception as exc:  # census: cleanup/containment (CR-13a)
        # The refusal has ALREADY been decided by the caller; this handler
        # runs on the reporting path behind it. It cannot turn a denial into
        # an admission -- there is no permission left to grant -- and letting
        # the exception escape would replace a constant refusal with an error
        # response that tells an attacker the audit store is down.
        log.error("control-plane refusal could not be audited",
                  control_path=ingress, reason=reason, error=repr(exc)[:200])


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

        Every check runs against `fstat` on the SAME descriptor the bytes come
        from, opened `O_NOFOLLOW`, so nothing can be swapped in between
        validating and reading (module docstring's TOCTOU claim, asserted by
        test_load_validates_and_reads_one_file_description).
        """
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            # ELOOP (a symlink, refused by O_NOFOLLOW), ENOENT, EACCES: all of
            # them mean the same thing to a caller -- no trustworthy store.
            raise CredentialStoreError("operator credential is unavailable") from exc
        try:
            raw = self._read_validated(fd)
        finally:
            os.close(fd)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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

    @staticmethod
    def _read_validated(fd: int) -> bytes:
        """fstat-gate an open descriptor, then read exactly what it promised.

        The size is re-checked after the read, and the descriptor is probed for
        EOF, because the first `fstat` decides how many bytes we consume: a
        writer appending between the two would leave us parsing a valid PREFIX
        and silently ignoring the rest.  A store that changed size under the
        read is not a store to authenticate against -- `rotate` swaps whole
        files with `os.replace` and never appends, so growth means someone
        else is writing.
        """
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CredentialStoreError("operator credential is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise CredentialStoreError("operator credential permissions must be 0600")
        if info.st_uid != os.geteuid():
            raise CredentialStoreError("operator credential has an unexpected owner")
        if info.st_nlink > 1:
            # A second name for this inode is a second, differently-permissioned
            # door to the secret -- and `rotate`'s os.replace never produces one.
            # `> 1`, not `!= 1`: zero links means the file was replaced after we
            # opened it, and THIS descriptor still holds exactly the validated
            # inode, so reading it is correct rather than suspect.
            raise CredentialStoreError("operator credential has more than one name")
        if not 0 < info.st_size <= _MAX_STORE_BYTES:
            raise CredentialStoreError("operator credential has an invalid size")
        try:
            raw = b""
            while len(raw) <= _MAX_STORE_BYTES:
                chunk = os.read(fd, _MAX_STORE_BYTES + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
            after = os.fstat(fd)
        except OSError as exc:
            raise CredentialStoreError("operator credential is unreadable") from exc
        # Read to EOF and compare against the size that was validated, rather
        # than consuming exactly that many bytes: a file that grew, shrank, or
        # was replaced under the read all land here instead of yielding a
        # plausible prefix.  The loop bound keeps an unbounded growth from
        # turning this into an unbounded allocation.
        if len(raw) != info.st_size or after.st_size != info.st_size:
            raise CredentialStoreError("operator credential changed while it was read")
        return raw

    def authenticate(self,
                     headers: email.message.Message | Mapping[str, str]) -> Actor | None:
        """Return the credential's named actor, or None for missing/invalid auth.

        The two REAL argument types, named directly: `http.server` hands over an
        `email.message.Message` (whose `get_all` is what makes duplicate-header
        detection possible), and callers without one pass a plain mapping.  A
        `Protocol` stub stood here and was deleted rather than tested: its body
        is a bare `...`, which coverage.py excludes by default, and an excluded
        line on a changed file is exactly what the diff-coverage gate rejects --
        correctly, since a type that cannot be executed cannot be proven.

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
                # repaired by rotating.
                os.replace(temp, self.path)
            except OSError:
                temp.unlink(missing_ok=True)
                raise
        except OSError as exc:
            # e.g. a read-only state dir, or a directory sitting where the
            # store belongs. Fail closed through this module's own error type
            # so no caller has to know about errno.  Reachable ONLY before the
            # swap, so a caller told "could not be rotated" still holds the
            # credential that works.
            raise CredentialStoreError(
                f"operator credential could not be rotated: {exc.strerror or exc}") from exc
        # Past the swap, `record` IS the live credential: every mutation now
        # authenticates against it and the previous value is already dead.
        # Nothing below may be reported as a rotation failure.
        self._sync_parent_directory()
        return record

    def _sync_parent_directory(self) -> None:
        """Make the completed rename durable; a failure downgrades to a warning.

        fsync-ing the directory is what keeps the swap (not just the bytes)
        across a crash.  When it fails -- a filesystem that refuses O_RDONLY on
        directories, a container overlay -- the rotation has ALREADY happened
        and the operator is holding the only copy of the value that now works.
        Raising here would report "rotation failed" for a rotation that
        succeeded and would strand the deployment with no usable credential,
        so this is stated honestly instead: the new value is live, and only its
        crash-durability is unproven.
        """
        try:
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            log.warning(
                "operator credential rotated, but its directory entry could "
                "not be made durable; the new credential is live and a crash "
                "before the next sync could restore the previous one",
                credential_path=str(self.path),
                reason=exc.strerror or str(exc))

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
