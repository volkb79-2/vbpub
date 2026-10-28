"""A Session ties a profile to a live, guarded RPC client and the adapter chosen for its API generation."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    missing_methods: dict[str, list[str]] = field(default_factory=dict)

    @property
    def workflows(self):
        """The shared query/action layer used by both CLI handlers and TUI screens."""
        from .workflows import Workflows
        return Workflows(self.rpc)

    def close(self) -> None:
        self.rpc.close()

    def banner(self) -> str:
        mode = "read-only" if self.rpc.read_only else "WRITE ENABLED"
        tls = "  TLS VERIFICATION OFF" if self.insecure_tls else ""
        gaps = ""
        if self.missing_methods:
            summary = "; ".join(f"{feature}: {', '.join(methods)}" for feature, methods in sorted(self.missing_methods.items()))
            gaps = f"  v27 capability gaps ({summary})"
        return f"{self.profile.name} {self.profile.host}  {self.backend.generation} ({self.backend.version})  {mode}{tls}{gaps}"

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
        available = rpc.call("core.get_methods", None, "WS")
        if not isinstance(available, Mapping) or any(not isinstance(method, str) for method in available):
            raise transport.TransportError("capability", "core.get_methods returned an invalid v27 WebSocket method list")
        rpc._set_live_capabilities(available)
        missing = adapters.missing_methods(backend, available)
    except Exception:
        rpc.close()
        raise
    return Session(profile, rpc, backend, insecure_tls=profile.insecure and insecure_ack, missing_methods=missing)
