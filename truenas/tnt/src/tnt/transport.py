"""Transport: one persistent JSON-RPC session over ``truenas_api_client``.

The API key is only ever passed to ``Client.login_with_api_key`` (which uses ``auth.login_ex``; the deprecated
``auth.login_with_api_key`` middleware method is never called). A login failure is fatal and is not retried:
TrueNAS rate-limits 20 unauthenticated attempts per 60 s and then locks out for 10 minutes.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Protocol
from urllib.parse import urlsplit

from .errors import Refused, TransportError
from .policy import SafetyState, canonical_params, is_read_method, normalise_method, refusal_for
from .profile import LOOPBACK, Profile, Secret


class RpcClient(Protocol):
    def call(self, method: str, *params: Any, job: bool = False) -> Any: ...
    def close(self) -> None: ...


class GuardedRpc:
    """The only object a screen or verb gets to call the host with; it enforces the policy itself.

    Reads are allowed in every session. A non-read method is refused in a read-only session, and in a write
    session it runs only inside ``authorized(...)``: the exact calls (method, parameters, job flag, in order) of a
    confirmed plan, each of which must also pass ``refusal_for``. ``authorized`` is meant to be entered only by
    ``actions.apply_plan``; nothing else in tnt calls it.
    """

    def __init__(self, inner: RpcClient, *, read_only: bool = True, secret: Secret | None = None):
        self._inner = inner
        self.read_only = read_only
        self._secret = secret
        self._grant: tuple[deque, SafetyState | None] | None = None
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @contextmanager
    def authorized(self, calls: Iterable[tuple[str, tuple[Any, ...], bool]], state: SafetyState | None) -> Iterator[None]:
        if self.read_only:
            raise Refused("read-only", "a read-only session cannot execute a plan")
        if self._grant is not None:
            raise Refused("nested-plan", "a plan is already executing")
        self._grant = (deque((normalise_method(m), canonical_params(p), bool(j)) for m, p, j in calls), state)
        try:
            yield
        finally:
            self._grant = None

    def call(self, method: str, *params: Any, job: bool = False) -> Any:
        name = normalise_method(method)
        if not is_read_method(name):
            reason = refusal_for(name, params, self._grant[1] if self._grant else None)
            if self.read_only:
                raise Refused("read-only", f"{name} is not a read method; this session has no write access (use --allow-write)")
            if reason:
                raise Refused("refusal-list", f"{name}: {reason}")
            if self._grant is None:
                raise Refused("no-plan", f"{name} is not part of a confirmed plan being applied; write sessions are default-deny")
            queue = self._grant[0]
            if not queue or queue[0] != (name, canonical_params(params), bool(job)):
                raise Refused("not-in-plan", f"{name} with these parameters is not the next call of the plan being applied")
            queue.popleft()
        elif (reason := refusal_for(name, params, None)):
            raise Refused("refusal-list", f"{name}: {reason}")
        self.calls.append((name, params))
        try:
            return self._inner.call(name, *params, job=job) if job else self._inner.call(name, *params)
        except Exception as exc:  # the client raises its own and websocket exceptions; one scrubbed error type here
            raise TransportError("call", self._scrub(f"{name} failed: {exc}")) from None

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
