"""A Session ties a profile to a live, guarded RPC client and the adapter chosen for its API generation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import adapters, transport
from .adapters.base import Backend
from .domain import Disk
from .profile import Profile, Secret, read_credential


@dataclass
class Session:
    profile: Profile
    rpc: transport.GuardedRpc
    backend: Backend
    insecure_tls: bool = False

    def close(self) -> None:
        self.rpc.close()

    def banner(self) -> str:
        mode = "read-only" if self.rpc.read_only else "WRITE ENABLED"
        tls = "  TLS VERIFICATION OFF" if self.insecure_tls else ""
        return f"{self.profile.name} {self.profile.host}  {self.backend.generation} ({self.backend.version})  {mode}{tls}"

    def disks(self) -> list[Disk]:
        """The one query path: the CLI `disks` verb and the TUI Storage > Disks view both call this."""
        return self.backend.list_disks(self.rpc)


SessionOpener = Callable[..., Session]


def open_session(profile: Profile, *, insecure_ack: bool = False, allow_write: bool = False, env: Mapping[str, str] | None = None,
                 secret: Secret | None = None, **connect_kwargs: Any) -> Session:
    """Connect, authenticate (once, no retry), detect the API generation, pick the adapter."""
    credential = secret or read_credential(profile, env)
    client = transport.connect(profile, credential, insecure_ack=insecure_ack, **connect_kwargs)
    rpc = transport.GuardedRpc(client, read_only=not allow_write, secret=credential)
    try:
        backend = adapters.detect(rpc)
    except Exception:
        rpc.close()
        raise
    return Session(profile, rpc, backend, insecure_tls=profile.insecure and insecure_ack)
