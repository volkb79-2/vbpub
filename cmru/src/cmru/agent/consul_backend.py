"""ConsulBackend — stdlib urllib implementation of DesiredStateBackend.

Talks ONLY to 127.0.0.1:8500 (the local Consul client agent, per spec §2).
Zero third-party dependencies: urllib.request only.

KV path layout (SPEC H):
  cmru/landscapes/<landscape>/nodes/<node>/desired
  cmru/landscapes/<landscape>/nodes/<node>/desired.sig  (optional)
  cmru/landscapes/<landscape>/nodes/<node>/observed
  cmru/landscapes/<landscape>/locks/<node>
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

from cmru.agent.backend import (
    DesiredStateBackend,
    EnrollmentSeed,
    LockHandle,
    NodeIdentity,
)

log = logging.getLogger(__name__)

_CONSUL_ADDR = "http://127.0.0.1:8500"
_SESSION_TTL = "30s"
_SESSION_LOCK_DELAY = "5s"


def _kv_desired(landscape: str, node: str) -> str:
    return f"cmru/landscapes/{landscape}/nodes/{node}/desired"


def _kv_desired_sig(landscape: str, node: str) -> str:
    return f"cmru/landscapes/{landscape}/nodes/{node}/desired.sig"


def _kv_observed(landscape: str, node: str) -> str:
    return f"cmru/landscapes/{landscape}/nodes/{node}/observed"


def _kv_lock(landscape: str, node: str) -> str:
    return f"cmru/landscapes/{landscape}/locks/{node}"


def _redact(s: str) -> str:
    """Redact a token for log output — show only first 4 chars."""
    if not s:
        return "(empty)"
    return s[:4] + "****"


class ConsulBackend(DesiredStateBackend):
    """Consul KV + sessions + service registration via stdlib HTTP.

    Authentication: pass the per-node ACL token via `token` arg; it is
    sent in the X-Consul-Token header and NEVER logged.
    """

    def __init__(
        self,
        consul_addr: str = _CONSUL_ADDR,
        token: Optional[str] = None,
        timeout: int = 10,
    ) -> None:
        self._addr = consul_addr.rstrip("/")
        self._token = token        # NEVER log this value
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._token:
            h["X-Consul-Token"] = self._token   # not logged
        return h

    def _get(self, path: str, params: Optional[dict] = None) -> Tuple[int, bytes, dict]:
        url = f"{self._addr}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            return exc.code, body, {}
        except urllib.error.URLError as exc:
            raise ConsulUnavailable(str(exc)) from exc

    def _put(self, path: str, body: bytes, params: Optional[dict] = None) -> Tuple[int, bytes]:
        url = f"{self._addr}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        req = urllib.request.Request(url, data=body, headers=self._headers(), method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            return exc.code, body
        except urllib.error.URLError as exc:
            raise ConsulUnavailable(str(exc)) from exc

    def _delete(self, path: str) -> Tuple[int, bytes]:
        url = f"{self._addr}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            return exc.code, body
        except urllib.error.URLError as exc:
            raise ConsulUnavailable(str(exc)) from exc

    # ------------------------------------------------------------------
    # DesiredStateBackend implementation
    # ------------------------------------------------------------------

    def enroll(self, seed: EnrollmentSeed) -> NodeIdentity:
        """Register service + write standby observed state; return identity.

        The seed's consul_token is used for the initial write; a per-node
        token (from auto_config) should replace it in production (SPEC H).
        """
        # Temporarily use the provisioning token for enrollment
        original_token = self._token
        self._token = seed.consul_token  # REDACTED in logs
        try:
            self.register_service(seed.node_id)
            # Write standby observed state
            from cmru.agent.protocol import ObservedState
            obs = ObservedState(health="healthy", message="standby")
            self.publish_observed(seed.node_id, seed.landscape, obs.to_json())
        finally:
            self._token = original_token

        return NodeIdentity(
            node_id=seed.node_id,
            landscape=seed.landscape,
            token_path=None,   # caller persists the token separately
            public_key=seed.minisign_pubkey,
        )

    def watch_desired(
        self,
        node_id: str,
        landscape: str,
        index: int,
        wait: str = "300s",
    ) -> "tuple[Optional[bytes], int]":
        """Blocking KV watch.  Returns (raw_bytes_or_None, new_index).

        Uses Consul blocking query: ?index=<N>&wait=<T>.
        On outage raises ConsulUnavailable — callers must catch and back off.
        """
        key = _kv_desired(landscape, node_id)
        status, body, headers = self._get(
            f"/v1/kv/{key}",
            params={"index": str(index), "wait": wait},
        )
        new_index = int(headers.get("X-Consul-Index", str(index)))

        if status == 404:
            return None, new_index
        if status != 200:
            raise ConsulUnavailable(f"KV GET {key} returned HTTP {status}")

        try:
            entries = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ConsulUnavailable(f"malformed KV response: {exc}") from exc

        if not entries:
            return None, new_index

        value_b64 = entries[0].get("Value")
        if value_b64 is None:
            return None, new_index
        raw = base64.b64decode(value_b64)
        return raw, new_index

    def acquire_lock(self, node_id: str, landscape: str, generation: int) -> LockHandle:
        """Create a Consul session and try to acquire the host lock.

        Returns LockHandle with acquired=True/False.
        Caller should retry if acquired=False.
        """
        # Create session
        session_body = json.dumps({
            "TTL": _SESSION_TTL,
            "Behavior": "delete",
            "LockDelay": _SESSION_LOCK_DELAY,
            "Name": f"cmru-agent-{node_id}-gen{generation}",
        }).encode()
        status, resp_body = self._put("/v1/session/create", session_body)
        if status != 200:
            raise ConsulUnavailable(f"session/create failed HTTP {status}")
        session_id = json.loads(resp_body)["ID"]

        # Try to acquire the lock key
        lock_key = _kv_lock(landscape, node_id)
        status, resp_body = self._put(
            f"/v1/kv/{lock_key}",
            f"gen={generation}".encode(),
            params={"acquire": session_id},
        )
        acquired = resp_body.strip() == b"true"
        return LockHandle(session_id=session_id, key=lock_key, acquired=acquired)

    def release_lock(self, lock: LockHandle) -> None:
        """Release the lock key and destroy the session."""
        try:
            self._put(
                f"/v1/kv/{lock.key}",
                b"",
                params={"release": lock.session_id},
            )
        except ConsulUnavailable:
            log.warning("Consul unavailable during lock release — session will TTL-expire")
        try:
            self._put(f"/v1/session/destroy/{lock.session_id}", b"")
        except ConsulUnavailable:
            log.warning("Consul unavailable during session destroy — session will TTL-expire")

    def publish_observed(
        self,
        node_id: str,
        landscape: str,
        observed_json: str,
    ) -> None:
        key = _kv_observed(landscape, node_id)
        status, _ = self._put(f"/v1/kv/{key}", observed_json.encode())
        if status not in (200, 201):
            log.warning("Failed to publish observed state: HTTP %s", status)

    def register_service(self, node_id: str) -> None:
        """Register/refresh cmru-agent service with a TTL health check."""
        payload = json.dumps({
            "ID": f"cmru-agent-{node_id}",
            "Name": "cmru-agent",
            "Tags": [node_id],
            "Check": {
                "CheckID": f"service:cmru-agent-{node_id}",
                "TTL": "60s",
                "DeregisterCriticalServiceAfter": "10m",
            },
        }).encode()
        status, _ = self._put("/v1/agent/service/register", payload)
        if status not in (200, 201):
            log.warning("Failed to register service: HTTP %s", status)

    def pass_health_check(self, node_id: str) -> None:
        """Pass the TTL health check — must be called before TTL expires."""
        check_id = f"service:cmru-agent-{node_id}"
        status, _ = self._put(f"/v1/agent/check/pass/{check_id}", b"")
        if status not in (200, 201):
            log.warning("Failed to pass health check %s: HTTP %s", check_id, status)

    def read_observed(self, node_id: str, landscape: str) -> Optional[str]:
        """Return current observed state JSON or None if absent."""
        key = _kv_observed(landscape, node_id)
        status, body, _ = self._get(f"/v1/kv/{key}")
        if status == 404:
            return None
        if status != 200:
            return None
        try:
            entries = json.loads(body)
            if not entries:
                return None
            value_b64 = entries[0].get("Value")
            if value_b64 is None:
                return None
            return base64.b64decode(value_b64).decode()
        except (json.JSONDecodeError, ValueError):
            return None

    def read_desired_sig(self, node_id: str, landscape: str) -> Optional[bytes]:
        """Return desired.sig bytes if present, else None."""
        key = _kv_desired_sig(landscape, node_id)
        status, body, _ = self._get(f"/v1/kv/{key}")
        if status == 404:
            return None
        if status != 200:
            return None
        try:
            entries = json.loads(body)
            if not entries:
                return None
            value_b64 = entries[0].get("Value")
            if value_b64 is None:
                return None
            return base64.b64decode(value_b64)
        except (json.JSONDecodeError, ValueError):
            return None


class ConsulUnavailable(OSError):
    """Raised when the local Consul agent cannot be reached or returns 5xx."""
