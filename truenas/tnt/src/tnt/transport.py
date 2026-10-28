"""Transport: one persistent JSON-RPC session over ``truenas_api_client``.

The API key is only ever passed to ``Client.login_with_api_key`` (which uses ``auth.login_ex``; the deprecated
``auth.login_with_api_key`` middleware method is never called). A login failure is fatal and is not retried:
TrueNAS rate-limits 20 unauthenticated attempts per 60 s and then locks out for 10 minutes.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from .errors import Refused, TransportError
from .profile import LOOPBACK, Profile, Secret


class RpcClient(Protocol):
    def call(self, method: str, *params: Any, job: bool = False) -> Any: ...
    def close(self) -> None: ...


# Method-name shapes a read-only session may call. Everything else needs an explicit write session.
READ_PREFIXES = ("system.info", "system.version", "core.get_methods", "auth.me", "auth.mechanism_choices")
READ_SUFFIXES = (".query", ".details", ".get_instance", ".config", ".get_disks", ".get_state", ".choices", ".pool_choices", ".get_used",
                 ".import_find", ".validate_name", ".temperatures")


def is_read_method(method: str) -> bool:
    return method in READ_PREFIXES or method.endswith(READ_SUFFIXES)


class GuardedRpc:
    """Wraps a client; a read-only session refuses every method that is not on the read list."""

    def __init__(self, inner: RpcClient, *, read_only: bool = True, secret: Secret | None = None):
        self._inner = inner
        self.read_only = read_only
        self._secret = secret
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def call(self, method: str, *params: Any, job: bool = False) -> Any:
        if self.read_only and not is_read_method(method):
            raise Refused("read-only", f"{method} is not a read method; this session has no write access (use --allow-write)")
        self.calls.append((method, params))
        try:
            return self._inner.call(method, *params, job=job) if job else self._inner.call(method, *params)
        except Exception as exc:  # the client raises its own and websocket exceptions; one scrubbed error type here
            raise TransportError("call", self._scrub(f"{method} failed: {exc}")) from None

    def close(self) -> None:
        try:
            self._inner.close()
        except Exception:  # closing a dead socket is not an error worth reporting
            pass

    def _scrub(self, text: str) -> str:
        return self._secret.scrub(text) if self._secret else text


def _default_client_factory(uri: str, verify_ssl: bool) -> Any:
    from truenas_api_client import Client  # imported late: the pure-logic modules stay importable without a socket stack
    return Client(uri=uri, verify_ssl=verify_ssl)


def connect(profile: Profile, secret: Secret, *, insecure_ack: bool = False, allow_loopback_ws: bool = False,
            client_factory: Callable[[str, bool], Any] = _default_client_factory) -> Any:
    """Open and authenticate a session; returns the raw client (wrap it in ``GuardedRpc``).

    ``insecure_ack`` is the explicit command-line acknowledgement that turns a profile's ``tls = "insecure"``
    into an unverified connection. ``allow_loopback_ws`` exists for the tests' fake server only: it is honoured
    for loopback hosts and is not reachable from the CLI or from a profile.
    """
    parts = urlsplit(profile.url)
    plain_ws = parts.scheme == "ws"
    if plain_ws and not (allow_loopback_ws and parts.hostname in LOOPBACK):
        raise TransportError("refused", "ws:// is refused: TrueNAS revokes API keys sent without TLS; use wss://")
    if parts.scheme not in ("ws", "wss"):
        raise TransportError("refused", f"unsupported URL scheme {parts.scheme!r}")
    if profile.insecure and not insecure_ack:
        raise TransportError("refused", f"profile {profile.name} has tls = \"insecure\": pass --insecure to confirm "
                                         "TLS verification is off for this throwaway host")
    verify = not (profile.insecure and insecure_ack)
    try:
        client = client_factory(profile.url, verify)
    except Exception as exc:
        raise TransportError("connect", secret.scrub(f"cannot connect to {profile.host}: {exc}")) from None
    try:
        from truenas_api_client import APIKeyAuthMech
        mech = APIKeyAuthMech.SCRAM if profile.auth == "scram" else APIKeyAuthMech.PLAIN
        client.login_with_api_key(profile.username, secret.reveal(), mech, channel_binding=not plain_ws and profile.auth == "scram")
    except Exception as exc:
        try:
            client.close()
        except Exception:  # the login failure is the error to report
            pass
        raise TransportError("auth", secret.scrub(f"login to {profile.host} as {profile.username} failed: {exc}")) from None
    return client
