"""A Session ties a profile to a live, guarded RPC client and the adapter chosen for its API generation."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import adapters, transport
from .adapters.base import Backend
from .domain import Disk
from .profile import Profile, Secret
from .errors import ProfileError, Refused
from .jobs import JobRegistry


_KMIP_TLS_VERSIONS = frozenset({"PROTOCOL_TLSv1", "PROTOCOL_TLSv1_1", "PROTOCOL_TLSv1_2"})


def _valid_kmip_config(value: Any) -> bool:
    """Check the required v27 KMIPEntry fields without exposing any configuration values."""
    if not isinstance(value, Mapping):
        return False
    certificate = value.get("certificate")
    certificate_authority = value.get("certificate_authority")
    server = value.get("server")
    port = value.get("port")
    ssl_version = value.get("ssl_version")
    return (
        isinstance(value.get("id"), int) and not isinstance(value.get("id"), bool)
        and all(isinstance(value.get(key), bool) for key in ("enabled", "manage_sed_disks", "manage_zfs_keys"))
        and (certificate is None or (isinstance(certificate, int) and not isinstance(certificate, bool)))
        and (certificate_authority is None
             or (isinstance(certificate_authority, int) and not isinstance(certificate_authority, bool)))
        and isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535
        and (server is None or (isinstance(server, str) and bool(server)))
        and isinstance(ssl_version, str) and ssl_version in _KMIP_TLS_VERSIONS
    )


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
    _generation: int = field(init=False, repr=False, default=0)
    _menu_capability_generation: int = field(init=False, repr=False, default=-1)
    _menu_capabilities: dict[str, dict[str, str | None]] | None = field(init=False, repr=False, default=None)
    _menu_capability_lock: threading.RLock = field(init=False, repr=False, default_factory=threading.RLock)

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
        self._set_generation(generation, force_invalidate=True)

    def use_job_registry(self, registry: JobRegistry, *, generation: int = 0) -> None:
        self.job_registry = registry
        self.rpc.attach_job_registry(registry, generation=generation)
        self._read_views.job_registry = registry
        self._set_generation(generation)

    def _set_generation(self, generation: int, *, force_invalidate: bool = False) -> None:
        with self._menu_capability_lock:
            if force_invalidate or self._generation != generation:
                self._generation = generation
                self._menu_capability_generation = -1
                self._menu_capabilities = None

    def conditional_menu_capabilities(self) -> dict[str, dict[str, str | None]]:
        """Probe v27 methods and device state behind conditional entries once per host generation."""
        with self._menu_capability_lock:
            if (self._menu_capabilities is not None
                    and self._menu_capability_generation == self._generation):
                return {key: dict(value) for key, value in self._menu_capabilities.items()}

            probes = (
                ("apps", "app.query", (), lambda value: isinstance(value, list)
                 and all(isinstance(row, Mapping) for row in value), False),
                ("containers", "container.query", ([], {}), lambda value: isinstance(value, list)
                 and all(isinstance(row, Mapping) for row in value), False),
                ("vm", "vm.supports_virtualization", (), lambda value: isinstance(value, bool), False),
                ("kmip", "kmip.config", (), _valid_kmip_config, False),
                ("viewenclosure", "enclosure2.query", (), lambda value: isinstance(value, list)
                 and all(isinstance(row, Mapping) for row in value), True),
            )
            result: dict[str, dict[str, str | None]] = {}
            for key, method, params, valid_shape, empty_means_unsupported in probes:
                try:
                    value = self.rpc.call(method, *params)
                except Refused as exc:
                    if exc.rule == "method-unavailable":
                        result[key] = {"state": "unsupported", "reason": "the v27 method is not reported by this host"}
                    else:
                        result[key] = {"state": "unavailable", "reason": self.rpc._scrub(str(exc))}
                    continue
                except Exception as exc:
                    result[key] = {"state": "unavailable", "reason": self.rpc._scrub(str(exc))}
                    continue
                if not valid_shape(value):
                    result[key] = {"state": "unavailable", "reason": f"{method} returned an unexpected v27 shape"}
                elif method == "vm.supports_virtualization" and value is False:
                    result[key] = {"state": "unsupported", "reason": "hardware or firmware virtualization support is unavailable"}
                elif empty_means_unsupported and not value:
                    result[key] = {"state": "unsupported", "reason": "the host reported no matching hardware"}
                else:
                    result[key] = {"state": "supported", "reason": None}
            self._menu_capabilities = result
            self._menu_capability_generation = self._generation
            return {key: dict(value) for key, value in result.items()}

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
