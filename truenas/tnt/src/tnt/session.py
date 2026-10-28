"""A Session ties a profile to a live, guarded RPC client and the adapter chosen for its API generation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import adapters, transport
from .adapters.base import Backend
from .domain import Disk
from .profile import Profile, Secret
from .errors import ProfileError
from .jobs import JobRegistry


@dataclass
class Session:
    profile: Profile
    rpc: transport.GuardedRpc
    backend: Backend
    insecure_tls: bool = False
    missing_methods: dict[str, list[str]] = field(default_factory=dict)
    credential: Secret | None = field(default=None, repr=False)
    job_registry: JobRegistry = field(init=False, repr=False)
    _workflows: Any = field(init=False, repr=False, default=None)
    _read_views: Any = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self.job_registry = JobRegistry(self.profile.name, scrub=self.rpc._scrub)
        self.rpc.attach_job_registry(self.job_registry)
        self._make_services()

    def _make_services(self) -> None:
        from .readviews import ReadViews
        from .workflows import Workflows
        self._workflows = Workflows(self.rpc)
        self._read_views = ReadViews(self.rpc, self.profile.name, self.job_registry)

    @property
    def workflows(self):
        """The shared query/action layer used by both CLI handlers and TUI screens."""
        return self._workflows

    @property
    def read_views(self):
        """Validated read models used by the overview CLI commands and TUI screens."""
        return self._read_views

    def replace_rpc(self, rpc: transport.GuardedRpc, *, generation: int = 0) -> None:
        """Replace a disconnected client while retaining this host's jobs and read services."""
        self.rpc = rpc
        rpc.attach_job_registry(self.job_registry, generation=generation)
        self._workflows.rpc = rpc
        self._read_views.rpc = rpc

    def use_job_registry(self, registry: JobRegistry, *, generation: int = 0) -> None:
        self.job_registry = registry
        self.rpc.attach_job_registry(registry, generation=generation)
        self._read_views.job_registry = registry

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


def open_session(profile: Profile, *, insecure_ack: bool = False, allow_write: bool = False,
                 secret: Secret | None = None, **connect_kwargs: Any) -> Session:
    """Connect, authenticate (once, no retry), detect the API generation, pick the adapter."""
    credential = secret
    if not isinstance(credential, Secret):
        raise ProfileError("credentials must be loaded from the paired tnt.secrets.toml before opening a session")
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
    return Session(profile, rpc, backend, insecure_tls=profile.insecure and insecure_ack, missing_methods=missing,
                   credential=credential)
