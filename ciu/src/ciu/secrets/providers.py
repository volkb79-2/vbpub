"""Vault KV2 provider for CIU v2 secrets.

Normative contract: docs/SPEC.md §S4 — specifically S4.15 (KV2 payload
shape / read extraction) and S4.16 (Vault address + token resolution order).

This module owns *all* Vault HTTP I/O for v2 secret materialization. It uses
only the standard library (``urllib``); there is no ``hvac`` dependency
(matching the v1 implementation in ``engine.py``).

Public API
----------
VaultError                              : RuntimeError with spec-ID messages
vault_addr_from_config(config) -> str   : S4.16 address from topology
resolve_vault_token(config, repo_root)  : S4.16 token source order
VaultKV2                                : KV2 read/write client (S4.15)
"""

from __future__ import annotations

import json
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ciu.config_constants import STACK_CONFIG_RENDERED


class VaultError(RuntimeError):
    """A Vault address / token / I/O failure.

    Messages carry the enforcing spec ID (e.g. ``[S4.16]`` / ``[S4.15]``)
    so failures are traceable to the contract. A VaultError MUST NOT contain
    a secret value (S4.23).
    """


# ---------------------------------------------------------------------------
# S4.16 — Vault address
# ---------------------------------------------------------------------------

def vault_addr_from_config(config: dict[str, Any]) -> str:
    """Build the Vault base address from ``topology.services.vault`` (S4.16).

    The caller is responsible for applying the active profile's
    ``topology_overrides`` (S7.4) to *config* before calling this — this
    function only reads the (possibly overridden) merged topology.

    Parameters
    ----------
    config : the merged config dict.

    Returns
    -------
    str : ``http://<internal_host>:<internal_port>`` (no trailing slash).

    Raises
    ------
    VaultError : when host or port is missing/empty.
    """
    services = config.get("topology", {}).get("services", {})
    vault_service = services.get("vault", {}) if isinstance(services, dict) else {}
    host = vault_service.get("internal_host") if isinstance(vault_service, dict) else None
    port = vault_service.get("internal_port") if isinstance(vault_service, dict) else None

    if not host or not port:
        raise VaultError(
            "[S4.16] topology.services.vault is missing internal_host/"
            "internal_port in the merged config; cannot build the Vault address"
        )

    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# S4.16 — Vault token source order
# ---------------------------------------------------------------------------

def resolve_vault_token(config: dict[str, Any], repo_root: Path) -> str | None:
    """Resolve a Vault token following the S4.16 source order.

    Order (first hit wins):
      1. ``VAULT_TOKEN`` in the process environment.
      2. The file named by ``config['vault']['token_file']`` (relative paths
         resolve against *repo_root*). An existing-but-unreadable file is a
         hard error (VaultError) — silently skipping it would mask a
         misconfiguration.
      3. The local Vault stack's rendered ``ciu.toml``: top-level
         ``[state].root_token``. The stack directory comes from
         ``config['vault']['stack_path']`` (default ``"infra/vault"``),
         resolved against *repo_root*. When that ``ciu.toml`` does not exist,
         this source yields nothing (the vault stack may simply not be
         deployed yet).

    Returns
    -------
    str | None : the token, or None when no source yields one.

    Raises
    ------
    VaultError : when a configured ``token_file`` exists but cannot be read.

    Notes
    -----
    The environment is read lazily here (via ``os.environ``) so callers and
    tests get the live value; ``os`` is imported inside the function to keep
    the precedence with explicit env reads obvious.
    """
    import os

    # (1) VAULT_TOKEN env
    env_token = os.environ.get("VAULT_TOKEN")
    if env_token:
        return env_token

    vault_cfg = config.get("vault", {})
    if not isinstance(vault_cfg, dict):
        vault_cfg = {}

    # (2) token_file
    token_file = vault_cfg.get("token_file")
    if token_file:
        tf_path = Path(token_file)
        if not tf_path.is_absolute():
            tf_path = repo_root / tf_path
        try:
            text = tf_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # A named-but-absent token_file falls through to the next source.
            pass
        except OSError as exc:
            raise VaultError(
                f"[S4.16] vault.token_file '{tf_path}' exists but could not "
                f"be read: {exc}"
            ) from exc
        else:
            token = text.strip()
            if token:
                return token

    # (3) local vault stack ciu.toml [state].root_token
    stack_path = vault_cfg.get("stack_path") or "infra/vault"
    stack_toml = repo_root / stack_path / STACK_CONFIG_RENDERED
    if stack_toml.is_file():
        try:
            with stack_toml.open("rb") as fh:
                doc = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise VaultError(
                f"[S4.16] could not read Vault stack state from '{stack_toml}': "
                f"{exc}"
            ) from exc
        state = doc.get("state", {})
        if isinstance(state, dict):
            root_token = state.get("root_token")
            if root_token:
                return str(root_token)

    return None


# ---------------------------------------------------------------------------
# S4.15 — Vault KV2 client
# ---------------------------------------------------------------------------

class VaultKV2:
    """Minimal Vault KV2 (version-2) read/write client (S4.15).

    Uses ``urllib`` only. All requests carry the ``X-Vault-Token`` header.
    The KV2 mount is assumed to be ``secret`` (the Vault default), matching
    the v1 engine (``/v1/secret/data/<path>``).
    """

    def __init__(self, addr: str, token: str, timeout: float = 10) -> None:
        self.addr = addr.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- low-level HTTP ----------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, str]:
        """Perform one KV2 request. Returns (status_code, body_text).

        A 404 is returned as ``(404, "")`` rather than raised, so the read
        path can map it to "absent". Other HTTP errors and transport errors
        surface as VaultError with the URL named.
        """
        url = f"{self.addr}/v1/secret/data/{path.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Vault-Token", self.token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return response.status, body
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, ""
            # S4.23: never include the response body for write requests —
            # Vault validation errors can echo the submitted payload (the
            # plaintext secret) back in the body. Reads carry no secret in
            # the request, so their body detail is safe and aids diagnosis.
            detail = ""
            if method == "GET":
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:  # pragma: no cover — best-effort detail only
                    detail = ""
            raise VaultError(
                f"[S4.16] Vault request {method} {url} failed "
                f"(HTTP {exc.code}){f': {detail}' if detail else ''}"
            ) from exc
        except urllib.error.URLError as exc:
            raise VaultError(
                f"[S4.16] could not reach Vault at {url}: {exc.reason}"
            ) from exc

    def _parse_json(self, body: str, url: str) -> dict:
        """Parse a JSON body, mapping a non-JSON/HTML body to VaultError.

        A misconfigured address (e.g. pointing at an HTML error page or a
        proxy) yields an HTML body; surfacing that as a clean message beats a
        raw ``JSONDecodeError`` traceback (S4.15 wording requirement).
        """
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise VaultError(
                f"Vault returned non-JSON response from {url}"
            ) from exc

    # -- KV2 read (S4.15 extraction) --------------------------------------

    def read(self, path: str, field: str | None = None) -> str | None:
        """Read a single secret value from KV2 path *path* (S4.15).

        Returns ``None`` when the path does not exist (HTTP 404).

        Extraction precedence for the stored data map:
          1. ``value`` key wins (the v2 canonical key, S4.15).
          2. otherwise, a single-key payload's sole value.
          3. otherwise, an explicit ``field`` selects that key.
          4. otherwise → VaultError listing the available keys and suggesting
             ``#<field>``.

        A ``field`` that is given but absent from the payload is a VaultError.
        """
        url = f"{self.addr}/v1/secret/data/{path.lstrip('/')}"
        status, body = self._request("GET", path)
        if status == 404:
            return None
        if not body:
            return None
        payload = self._parse_json(body, url)

        data = payload.get("data", {}).get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not data:
            return None

        # An explicit field selector that is present in the payload always
        # refers to that exact key — honour it before the single-key shortcut
        # so '#field' against a single-key map still validates the name.
        if field is not None:
            if field in data:
                return str(data[field])
            raise VaultError(
                f"[S4.15] Vault secret at '{path}' has no field '{field}'; "
                f"available keys: {sorted(data.keys())}"
            )

        # (1) canonical 'value' key wins.
        if "value" in data:
            return str(data["value"])

        # (2) single-key payload's sole value.
        if len(data) == 1:
            return str(next(iter(data.values())))

        # (4) ambiguous multi-key payload with no selector.
        keys = sorted(data.keys())
        suggestion = keys[0] if keys else "<field>"
        raise VaultError(
            f"[S4.15] Vault secret at '{path}' has multiple keys {keys} and "
            f"no 'value' key; select one with '{path}#{suggestion}'"
        )

    # -- KV2 write (S4.15 canonical key) ----------------------------------

    def write(self, path: str, value: str) -> None:
        """Write *value* to KV2 path *path* as ``{"value": value}`` (S4.15).

        Exactly the single canonical key is stored — the v1 suffix-based
        alias keys (``password``/``access_key``/...) are withdrawn.
        """
        url = f"{self.addr}/v1/secret/data/{path.lstrip('/')}"
        status, body = self._request("POST", path, {"data": {"value": value}})
        if status >= 400:
            raise VaultError(
                f"[S4.16] Vault write to {url} failed (HTTP {status})"
            )
