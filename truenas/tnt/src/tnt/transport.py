"""Transport: one persistent JSON-RPC session over ``truenas_api_client``.

API keys use ``Client.login_with_api_key``/SCRAM and passwords use the pinned client's
``Client.login_with_password``/``PASSWORD_PLAIN`` flow. A login failure is fatal and is not retried:
TrueNAS rate-limits 20 unauthenticated attempts per 60 s and then locks out for 10 minutes.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol
from urllib.parse import urlsplit

from .errors import Refused, TransportError
from .policy import SafetyState, canonical_params, is_read_method, normalise_method, refusal_for
from .profile import LOOPBACK, Profile, Secret


class RpcClient(Protocol):
    def call(self, method: str, *params: Any, job: bool = False, callback: Any = None) -> Any: ...
    def close(self) -> None: ...


_APPLY_AUTHORITY = object()


@dataclass(frozen=True)
class _ExecutionGrant:
    """One-use internal permit created only by the action executor after all plan gates pass."""

    rpc: Any
    nonce: str
    fingerprint: str
    calls: tuple[tuple[str, tuple[Any, ...], bool], ...]
    state: SafetyState | None


_SENSITIVE_KEYS = frozenset({"password", "old_password", "new_password", "api_key", "sshpubkey", "private_key",
                             "credentials", "credential", "token", "secret", "secret_access_key", "access_key",
                             "client_secret", "encryption_password", "encryption_salt", "key", "client_key", "salt",
                             "stored_key", "server_key"})


def _rpc_values(value: Any, *, reveal: bool = False, key: Any = None) -> Any:
    if isinstance(value, Secret):
        return value.reveal() if reveal else "***"
    if isinstance(value, dict):
        return {k: _rpc_values(v, reveal=reveal, key=k) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_rpc_values(v, reveal=reveal) for v in value)
    if isinstance(value, list):
        return [_rpc_values(v, reveal=reveal) for v in value]
    if not reveal and isinstance(key, str) and key.casefold() in _SENSITIVE_KEYS:
        return "***"
    return value


def _rpc_secrets(value: Any, key: Any = None) -> Iterable[Secret]:
    if isinstance(value, Secret):
        yield value
    elif isinstance(value, dict):
        for name, child in value.items():
            if isinstance(name, str) and name.casefold() in _SENSITIVE_KEYS and isinstance(child, str):
                yield Secret(child)
            else:
                yield from _rpc_secrets(child, name)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _rpc_secrets(child)


class GuardedRpc:
    """The only object a screen or verb gets to call the host with; it enforces the policy itself.

    Reads are allowed in every session. A non-read method is refused in a read-only session, and in a write
    session it runs only inside the private ``_authorized(...)`` path, with a one-use grant issued by
    ``actions.apply_plan`` after validation, live-state checks, confirmation and drift checks. Each call must also
    pass ``refusal_for``. The process is a trust boundary; the private capability prevents accidental bypasses.
    """

    def __init__(self, inner: RpcClient, *, read_only: bool = True, secret: Secret | None = None):
        self._inner = inner
        self.read_only = read_only
        self._secret = secret
        self._grant: tuple[deque, SafetyState | None] | None = None
        self._owner: tuple[int, int | None] | None = None
        self._lock = threading.RLock()
        self._spent: set[str] = set()
        self._issued_plans: dict[str, str] = {}
        self._execution_grants: dict[str, _ExecutionGrant] = {}
        self._live_capabilities: frozenset[str] | None = None
        self._event_subscriptions: set[str] = set()
        self.session_id = uuid.uuid4().hex
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @staticmethod
    def _identity() -> tuple[int, int | None]:
        """Who is calling: the thread and, inside an event loop, the asyncio task."""
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        return (threading.get_ident(), None if task is None else id(task))

    def _issue_plan(self, nonce: str, fingerprint: str) -> None:
        """Register an internally created plan identity so a copied/edited Plan cannot invent a fresh nonce."""
        with self._lock:
            if not nonce or nonce in self._issued_plans:
                raise Refused("plan-reused", "this plan already has an identity; plan again")
            self._issued_plans[nonce] = fingerprint

    def check_plan(self, nonce: str, fingerprint: str) -> None:
        """Check issuance and prior use before reading live state or spending the plan."""
        with self._lock:
            if (not nonce or nonce in self._spent or nonce not in self._issued_plans
                    or self._issued_plans[nonce] != fingerprint):
                raise Refused("plan-reused", "this plan was already applied or has no issued identity; plan again")

    def spend(self, nonce: str, fingerprint: str | None = None,
              calls: Iterable[tuple[str, tuple[Any, ...], bool]] = (), state: SafetyState | None = None,
              *, authority: object | None = None, require_grant: bool = True) -> _ExecutionGrant | None:
        """Spend an issued plan identity and optionally issue its private, exact-call execution grant."""
        if authority is not _APPLY_AUTHORITY:
            raise Refused("apply-capability", "plan execution grants can only be issued by actions.apply_plan")
        exact_calls = tuple((normalise_method(method), canonical_params(params), bool(job))
                            for method, params, job in calls)
        with self._lock:
            if (not nonce or nonce in self._spent or nonce not in self._issued_plans
                    or self._issued_plans[nonce] != fingerprint):
                raise Refused("plan-reused", "this plan was already applied or has no issued identity; plan again")
            self._spent.add(nonce)
            if not require_grant:
                return None
            grant = _ExecutionGrant(self, nonce, str(fingerprint), exact_calls, state)
            self._execution_grants[nonce] = grant
            return grant

    @contextmanager
    def _authorized(self, calls: Iterable[tuple[str, tuple[Any, ...], bool]], grant: _ExecutionGrant | None) -> Iterator[None]:
        if self.read_only:
            raise Refused("read-only", "a read-only session cannot execute a plan")
        exact_calls = tuple((normalise_method(method), canonical_params(params), bool(job))
                            for method, params, job in calls)
        with self._lock:
            if not isinstance(grant, _ExecutionGrant) or grant.rpc is not self:
                raise Refused("apply-capability", "only an internal apply_plan grant can authorize calls")
            issued = self._execution_grants.pop(grant.nonce, None)
            if (issued is not grant or grant.nonce not in self._spent or not grant.fingerprint
                    or grant.calls != exact_calls):
                raise Refused("apply-capability", "this execution grant is invalid, spent, or does not match these calls")
            if self._grant is not None:
                raise Refused("nested-plan", "a plan is already executing")
            self._grant = (deque(grant.calls), grant.state)
            self._owner = self._identity()
        try:
            yield
        finally:
            with self._lock:
                self._grant = None
                self._owner = None

    def authorized(self, calls: Iterable[tuple[str, tuple[Any, ...], bool]], state: SafetyState | None) -> Iterator[None]:
        """Reject the former public grant API; writes must pass through ``actions.apply_plan``."""
        raise Refused("apply-capability", "direct authorization is disabled; use actions.apply_plan")

    def _set_live_capabilities(self, available: Mapping[str, Any]) -> None:
        """Install the method names returned by core.get_methods at session open."""
        if not isinstance(available, Mapping):
            raise Refused("method-capabilities", "core.get_methods did not return a method metadata object")
        methods: set[str] = set()
        for method in available:
            methods.add(normalise_method(method))
        with self._lock:
            if self._live_capabilities is not None:
                raise Refused("method-capabilities", "live method capabilities are already set for this session")
            self._live_capabilities = frozenset(methods)

    def call(self, method: str, *params: Any, job: bool = False,
             job_callback: Callable[[Mapping[str, Any]], None] | None = None) -> Any:
        with self._lock:  # one call at a time: another thread or task can never interleave with a plan's calls
            return self._call(method, params, job, job_callback)

    def subscribe_event(self, name: str, callback: Callable[..., Any]) -> str:
        """Subscribe to the one reviewed read-only event source used by container inventory."""
        if name != "container.metrics" or not callable(callback):
            raise Refused("event-not-allowed", "only the container.metrics read event is available")
        subscribe = getattr(self._inner, "subscribe", None)
        if not callable(subscribe):
            raise TransportError("event", "the connected client does not support event subscriptions")
        try:
            subscription_id = subscribe(name, callback, sync=True)
        except Exception as exc:
            raise TransportError("event", self._scrub(f"cannot subscribe to {name}: {exc}")) from None
        if not isinstance(subscription_id, str) or not subscription_id:
            raise TransportError("event", f"{name} returned an invalid subscription id")
        with self._lock:
            self._event_subscriptions.add(subscription_id)
        return subscription_id

    def unsubscribe_event(self, subscription_id: str) -> None:
        """Unsubscribe only an event subscription issued by this guarded session."""
        with self._lock:
            if not isinstance(subscription_id, str) or subscription_id not in self._event_subscriptions:
                raise Refused("event-subscription", "this session does not own that event subscription")
            self._event_subscriptions.remove(subscription_id)
        unsubscribe = getattr(self._inner, "unsubscribe", None)
        if not callable(unsubscribe):
            raise TransportError("event", "the connected client cannot cancel event subscriptions")
        try:
            unsubscribe(subscription_id)
        except Exception as exc:
            raise TransportError("event", self._scrub(f"cannot unsubscribe from container.metrics: {exc}")) from None

    def _call(self, method: str, params: tuple[Any, ...], job: bool,
              job_callback: Callable[[Mapping[str, Any]], None] | None = None) -> Any:
        name = normalise_method(method)
        if self._live_capabilities is not None and name not in self._live_capabilities:
            raise Refused("method-unavailable", f"{name} is not reported by the live v27 capability list; this method is unavailable on this host")
        if not is_read_method(name):
            reason = refusal_for(name, params, self._grant[1] if self._grant else None)
            if self.read_only:
                raise Refused("read-only", f"{name} is not a read method; this session has no write access (use --allow-write)")
            if reason:
                raise Refused("refusal-list", f"{name}: {reason}")
            if self._grant is None:
                raise Refused("no-plan", f"{name} is not part of a confirmed plan being applied; write sessions are default-deny")
            if self._owner != self._identity():
                raise Refused("foreign-caller", f"{name}: the plan being applied belongs to another thread or task")
            queue = self._grant[0]
            if not queue or queue[0] != (name, canonical_params(params), bool(job)):
                raise Refused("not-in-plan", f"{name} with these parameters is not the next call of the plan being applied")
            queue.popleft()
        elif (reason := refusal_for(name, params, None)):
            raise Refused("refusal-list", f"{name}: {reason}")
        self.calls.append((name, _rpc_values(params)))
        actual_params = tuple(_rpc_values(p, reveal=True) for p in params)
        try:
            if job:
                if job_callback is not None:
                    return self._inner.call(name, *actual_params, job=True, callback=job_callback)
                return self._inner.call(name, *actual_params, job=True)
            return self._inner.call(name, *actual_params)
        except Exception as exc:  # the client raises its own and websocket exceptions; one scrubbed error type here
            message = f"{name} failed: {exc}"
            for secret in _rpc_secrets(params):
                message = secret.scrub(message)
            raise TransportError("call", self._scrub(message)) from None

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
        raise TransportError("refused", "ws:// is refused: credentials require TLS; use wss://")
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
        if profile.auth == "api_key":
            from truenas_api_client import APIKeyAuthMech
            client.login_with_api_key(profile.username, secret.reveal(), APIKeyAuthMech.SCRAM, channel_binding=not plain_ws)
        elif profile.auth == "password":
            client.login_with_password(profile.username, secret.reveal())
        else:
            raise ValueError(f"unsupported authentication mode {profile.auth!r}")
    except Exception as exc:
        try:
            client.close()
        except Exception:  # the login failure is the error to report
            pass
        raise TransportError("auth", secret.scrub(f"login to {profile.host} as {profile.username} failed: {exc}")) from None
    return client
