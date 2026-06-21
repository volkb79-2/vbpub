"""DesiredStateBackend ABC + dataclasses for Seam 2 (spec §3.1).

Concrete implementation: ConsulBackend in consul_backend.py.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class NodeIdentity:
    """Identity returned by enroll(); persisted to state dir."""
    node_id: str
    landscape: str
    token_path: Optional[str] = None      # path to the per-node ACL token file
    public_key: str = ""                  # minisign public key from enrollment seed


@dataclass
class EnrollmentSeed:
    """Bootstrap data consumed by enroll().
    Secrets MUST NOT be committed — pass at runtime only.
    """
    node_id: str
    landscape: str
    consul_token: str             # one-time provisioning token (REDACTED from logs)
    minisign_pubkey: str          # public key used to verify manifests


@dataclass
class LockHandle:
    """Opaque lock handle; released by release()."""
    session_id: str
    key: str
    acquired: bool

    def release(self, backend: "DesiredStateBackend", node_id: str) -> None:  # type: ignore[name-defined]
        backend.release_lock(self)


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class DesiredStateBackend(abc.ABC):
    """Abstract interface for the desired-state transport (Seam 2)."""

    @abc.abstractmethod
    def enroll(self, seed: "EnrollmentSeed") -> NodeIdentity:
        """Register the node, write initial standby observed state; return identity."""
        ...

    @abc.abstractmethod
    def watch_desired(
        self,
        node_id: str,
        landscape: str,
        index: int,
        wait: str,
    ) -> "tuple[Optional[bytes], int]":
        """Long-poll desired state. Returns (raw_json_bytes_or_None, new_index).

        raw_json_bytes is None when the key does not exist yet (standby).
        Blocks up to `wait` (e.g. '300s') on the Consul blocking-query endpoint.
        """
        ...

    @abc.abstractmethod
    def acquire_lock(self, node_id: str, landscape: str, generation: int) -> LockHandle:
        """Create a Consul session and acquire a host lock for the given generation.
        Returns a LockHandle; caller must call release_lock() or let session TTL expire.
        """
        ...

    @abc.abstractmethod
    def release_lock(self, lock: LockHandle) -> None:
        """Release the lock and destroy the session."""
        ...

    @abc.abstractmethod
    def publish_observed(
        self,
        node_id: str,
        landscape: str,
        observed_json: str,
    ) -> None:
        """Write observed state JSON to the backend KV store."""
        ...

    @abc.abstractmethod
    def register_service(self, node_id: str) -> None:
        """Register/refresh cmru-agent service with a TTL health check."""
        ...

    @abc.abstractmethod
    def pass_health_check(self, node_id: str) -> None:
        """Mark the TTL health check as passing."""
        ...

    @abc.abstractmethod
    def read_observed(self, node_id: str, landscape: str) -> Optional[str]:
        """Return current observed state JSON or None if absent."""
        ...
