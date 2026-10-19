"""Shared TrueNAS WebSocket API client used by the CLI frontends.

The upstream TrueNAS client owns protocol framing, authentication, event
subscriptions, and job tracking. This module adds the small project boundary:
strict local configuration, secure endpoint validation, generic call helpers,
and lazy loading that avoids a name collision with this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import http.client
from importlib.metadata import PackageNotFoundError, distribution
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import sys
import threading
import time
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

import tomllib


SETTINGS_PATH = Path(__file__).resolve().with_name("truenas.toml")
ENV_PATH = Path(__file__).resolve().with_name(".env")
API_KEY_ENV = "TRUENAS_API_KEY"
UPSTREAM_PACKAGE = "truenas_api_client"
UPSTREAM_MODULE_ALIAS = "_vbpub_official_truenas_api_client"
CONFIG_SCHEMA_VERSION = 1


class TrueNASAPIError(RuntimeError):
    """A configuration, connection, authentication, or API call failed."""


@dataclass(frozen=True)
class TrueNASSettings:
    """The non-secret connection settings for one TrueNAS system."""

    uri: str
    username: str
    verify_ssl: bool


def load_env_file(path: Path | None = None) -> None:
    """Load the local secret file without overriding the caller's environment."""

    env_path = path or ENV_PATH
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TrueNASAPIError(f"could not read environment file {env_path}: {exc}") from exc

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0:
                raise TrueNASAPIError(f"unterminated quoted value for {key} in {env_path}")
            value = value[1:end]
        elif "#" in value:
            value = value.split("#", 1)[0].rstrip()
        os.environ.setdefault(key, value)


def load_settings(path: Path | None = None) -> TrueNASSettings:
    """Load the closed, versioned settings schema; missing facts are never guessed."""

    settings_path = path or Path(os.environ.get("TRUENAS_CONFIG", SETTINGS_PATH))
    try:
        document = tomllib.loads(settings_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TrueNASAPIError(
            f"could not read TrueNAS settings {settings_path}: {exc}; "
            "copy truenas.toml.example to truenas.toml and edit its required values"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise TrueNASAPIError(f"invalid TOML in {settings_path}: {exc}") from exc

    if not isinstance(document, dict) or set(document) != {"schema_version", "api"}:
        raise TrueNASAPIError(
            f"{settings_path} must contain schema_version and the [api] table"
        )
    schema_version = document["schema_version"]
    if type(schema_version) is not int or schema_version != CONFIG_SCHEMA_VERSION:
        raise TrueNASAPIError(
            f"unsupported schema_version in {settings_path}: {schema_version!r}; "
            f"this CLI supports schema version {CONFIG_SCHEMA_VERSION}"
        )
    api = document["api"]
    if not isinstance(api, dict):
        raise TrueNASAPIError(f"[api] in {settings_path} must be a table")
    required = {"uri", "username", "verify_ssl"}
    missing = sorted(required - set(api))
    unknown = sorted(set(api) - required)
    if missing or unknown:
        parts = []
        if missing:
            parts.append("missing required key(s): " + ", ".join(missing))
        if unknown:
            parts.append("unknown key(s): " + ", ".join(unknown))
        raise TrueNASAPIError(f"invalid [api] settings in {settings_path}: {'; '.join(parts)}")

    uri = api["uri"]
    username = api["username"]
    verify_ssl = api["verify_ssl"]
    if not isinstance(uri, str) or not uri.strip():
        raise TrueNASAPIError(f"api.uri in {settings_path} must be a non-empty URL")
    if not isinstance(username, str) or not username.strip():
        raise TrueNASAPIError(f"api.username in {settings_path} must be a non-empty username")
    if not isinstance(verify_ssl, bool):
        raise TrueNASAPIError(f"api.verify_ssl in {settings_path} must be true or false")

    parsed = urlsplit(uri)
    if (
        parsed.scheme != "wss"
        or not parsed.hostname
        or parsed.path != "/api/current"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise TrueNASAPIError(
            "api.uri must be a secure TrueNAS v25.04+ endpoint such as "
            "wss://truenas.example.test/api/current"
        )
    return TrueNASSettings(uri=uri, username=username, verify_ssl=verify_ssl)


def api_key_from_environment() -> str:
    """Return the required API key without putting it in settings or argv."""

    key = os.environ.get(API_KEY_ENV, "")
    if not key.strip():
        raise TrueNASAPIError(
            f"missing {API_KEY_ENV}; set it in scripts/truenas/.env or the process environment"
        )
    return key


@lru_cache(maxsize=1)
def _load_official_client_module() -> Any:
    """Load the installed upstream package under an alias.

    The shared project module is explicitly named ``truenas_api_client.py``;
    importing the upstream package by its normal name would resolve back to
    this file when a frontend runs from this directory. Loading its installed
    package under a distinct package name preserves its relative imports.
    """

    try:
        installed = distribution(UPSTREAM_PACKAGE)
    except PackageNotFoundError as exc:
        raise TrueNASAPIError(
            "the official TrueNAS API client is not installed; "
            "run python -m pip install -r requirements.txt"
        ) from exc

    package_init = installed.locate_file(f"{UPSTREAM_PACKAGE}/__init__.py")
    if not Path(package_init).is_file():
        raise TrueNASAPIError(
            f"installed {UPSTREAM_PACKAGE} package does not contain its Python module"
        )
    spec = spec_from_file_location(
        UPSTREAM_MODULE_ALIAS,
        package_init,
        submodule_search_locations=[str(Path(package_init).parent)],
    )
    if spec is None or spec.loader is None:
        raise TrueNASAPIError("could not load the official TrueNAS API client package")
    module = module_from_spec(spec)
    sys.modules[UPSTREAM_MODULE_ALIAS] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(UPSTREAM_MODULE_ALIAS, None)
        raise
    return module


class TrueNASAPIClient:
    """Persistent authenticated connection with generic API and event methods."""

    def __init__(self, settings: TrueNASSettings, api_key: str):
        self.settings = settings
        self._api_key = api_key
        self._client: Any | None = None

    def __enter__(self) -> "TrueNASAPIClient":
        if self._client is not None:
            raise TrueNASAPIError("TrueNAS API client is already connected")
        upstream = _load_official_client_module()
        try:
            self._client = upstream.Client(
                uri=self.settings.uri,
                verify_ssl=self.settings.verify_ssl,
            )
            # The official client opens its WebSocket in the context manager.
            # Login before entering it would send auth against an unopened socket.
            self._client.__enter__()
            # This Python helper is a convenience wrapper; it selects the
            # server's auth.login_ex exchange on v27. Do not RPC the removed
            # auth.login_with_api_key method directly.
            self._client.login_with_api_key(self.settings.username, self._api_key)
        except Exception as exc:
            if self._client is not None:
                try:
                    self._client.__exit__(None, None, None)
                except Exception:
                    pass
            self._client = None
            raise TrueNASAPIError(f"could not connect or authenticate to TrueNAS: {exc}") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._client is not None:
            try:
                self._client.__exit__(exc_type, exc, traceback)
            finally:
                self._client = None

    def _connected(self) -> Any:
        if self._client is None:
            raise TrueNASAPIError("TrueNAS API client is not connected")
        return self._client

    def call(self, method: str, *params: Any) -> Any:
        """Call any method name supported by the server, with positional params."""

        try:
            return self._connected().call(method, *params)
        except Exception as exc:
            raise TrueNASAPIError(f"TrueNAS API method {method!r} failed: {exc}") from exc

    def call_job(
        self,
        method: str,
        *params: Any,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> Any:
        """Call a job method, forwarding progress and waiting for terminal state."""

        try:
            job = self._connected().call(
                method,
                *params,
                job="RETURN",
                callback=on_update,
            )
            return job.result()
        except Exception as exc:
            raise TrueNASAPIError(f"TrueNAS API job {method!r} failed: {exc}") from exc

    def upload_job_file(
        self,
        method: str,
        params: Sequence[Any],
        local_path: Path | str,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Stream one local file to an API job and return its TrueNAS job id.

        TrueNAS accepts job input files over HTTPS ``/_upload`` as multipart
        form data. The WebSocket API call names the job and its positional
        arguments; the file bytes stream separately in the ``file`` field.
        """

        if not isinstance(method, str) or not method.strip():
            raise TrueNASAPIError("upload job method must be a non-empty string")
        if not isinstance(params, Sequence) or isinstance(params, (str, bytes, bytearray)):
            raise TrueNASAPIError("upload job params must be a sequence of positional values")
        if not self.settings.verify_ssl:
            raise TrueNASAPIError(
                "TrueNAS file upload authenticates with a Bearer API key over HTTPS; "
                "certificate verification must be enabled for uploads"
            )
        path = Path(local_path)
        try:
            source = path.open("rb")
        except OSError as exc:
            raise TrueNASAPIError(f"could not open local upload file {path}: {exc}") from exc

        with source:
            try:
                source_stat = os.fstat(source.fileno())
            except OSError as exc:
                raise TrueNASAPIError(f"could not inspect local upload file {path}: {exc}") from exc
            if not stat.S_ISREG(source_stat.st_mode):
                raise TrueNASAPIError(f"local upload path is not a regular file: {path}")
            file_size = source_stat.st_size
            if file_size <= 0:
                raise TrueNASAPIError(f"local upload file is empty: {path}")

            parsed = urlsplit(self.settings.uri)
            if not parsed.hostname:
                raise TrueNASAPIError("configured TrueNAS WebSocket URL has no host")
            boundary = "----truenas-api-client-" + secrets.token_hex(16)
            payload = json.dumps(
                {"method": method, "params": list(params)},
                separators=(",", ":"),
            )
            data_field = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="data"\r\n\r\n'
                f"{payload}\r\n"
            ).encode("utf-8")
            file_header = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="upload.bin"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("ascii")
            closing = f"\r\n--{boundary}--\r\n".encode("ascii")
            content_length = len(data_field) + len(file_header) + file_size + len(closing)

            context = (
                ssl.create_default_context()
                if self.settings.verify_ssl
                else ssl._create_unverified_context()
            )
            connection = http.client.HTTPSConnection(
                parsed.hostname,
                parsed.port,
                context=context,
                timeout=60,
            )
            try:
                connection.putrequest("POST", "/_upload")
                connection.putheader("Authorization", f"Bearer {self._api_key}")
                connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
                connection.putheader("Content-Length", str(content_length))
                connection.endheaders()
                connection.send(data_field)
                connection.send(file_header)
                sent = 0
                if on_progress is not None:
                    on_progress(0, file_size)
                while sent < file_size:
                    block = source.read(min(1024 * 1024, file_size - sent))
                    if not block:
                        raise TrueNASAPIError(
                            f"local upload file changed while it was being read: {path}"
                        )
                    connection.send(block)
                    sent += len(block)
                    if on_progress is not None:
                        on_progress(sent, file_size)
                if source.read(1):
                    raise TrueNASAPIError(
                        f"local upload file grew while it was being read: {path}"
                    )
                connection.send(closing)
                response = connection.getresponse()
                response_body = response.read(65536)
            except TrueNASAPIError:
                raise
            except Exception as exc:
                raise TrueNASAPIError(
                    f"could not upload {path} to TrueNAS job {method!r}: {exc}"
                ) from exc
            finally:
                connection.close()

        if response.status != 200:
            detail = response_body.decode("utf-8", errors="replace").strip()
            if len(detail) > 1000:
                detail = detail[:1000] + "…"
            raise TrueNASAPIError(
                f"TrueNAS file upload endpoint returned HTTP {response.status}"
                + (f": {detail}" if detail else "")
            )
        try:
            result = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrueNASAPIError("TrueNAS upload endpoint returned invalid JSON") from exc
        job_id = result.get("job_id") if isinstance(result, dict) else None
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
            raise TrueNASAPIError(
                f"TrueNAS upload endpoint returned an invalid job_id: {result!r}"
            )
        return job_id

    def wait_job(
        self,
        job_id: int,
        *,
        on_update: Callable[[dict[str, Any]], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Follow one job's event changes and return its terminal record."""

        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
            raise TrueNASAPIError(f"job id must be a positive integer, got {job_id!r}")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise TrueNASAPIError("job timeout must be greater than zero")
        changed = threading.Event()
        started = time.monotonic()

        def on_event(message_type: str, **message: Any) -> None:
            fields = message.get("fields")
            if not isinstance(fields, dict) or fields.get("id") != job_id:
                return
            changed.set()

        subscription_id = self.subscribe("core.get_jobs", on_event, sync=True)
        try:
            while True:
                rows = self.call("core.get_jobs", [["id", "=", job_id]])
                if not isinstance(rows, list):
                    raise TrueNASAPIError(
                        "TrueNAS returned an unexpected core.get_jobs shape while waiting "
                        f"for job {job_id}: expected a list"
                    )
                matches = [
                    row for row in rows
                    if isinstance(row, dict) and row.get("id") == job_id
                ]
                if not matches:
                    raise TrueNASAPIError(f"TrueNAS has no visible job with id {job_id}")
                if len(matches) != 1:
                    raise TrueNASAPIError(f"TrueNAS returned multiple jobs with id {job_id}")
                record = dict(matches[0])
                if on_update is not None:
                    on_update(record)
                state_value = record.get("state")
                state = state_value.strip().upper() if isinstance(state_value, str) else None
                if state in {"SUCCESS", "FAILED", "ABORTED"}:
                    if state != "SUCCESS":
                        detail = record.get("error") or record.get("exception")
                        raise TrueNASAPIError(
                            f"TrueNAS job {job_id} finished in state {state}"
                            + (f": {detail}" if detail else "")
                        )
                    return record
                if timeout_seconds is not None:
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TrueNASAPIError(
                            f"timed out waiting for TrueNAS job {job_id}; "
                            f"last state was {state or 'unknown'}"
                        )
                    changed.wait(min(remaining, 30.0))
                else:
                    changed.wait(30.0)
                changed.clear()
        finally:
            self.unsubscribe(subscription_id)

    def subscribe(
        self,
        event: str,
        callback: Callable[..., None],
        *,
        sync: bool = True,
    ) -> str:
        """Subscribe to one server event on this persistent connection."""

        try:
            return self._connected().subscribe(event, callback, sync=sync)
        except Exception as exc:
            raise TrueNASAPIError(f"could not subscribe to TrueNAS event {event!r}: {exc}") from exc

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription previously created by :meth:`subscribe`."""

        try:
            self._connected().unsubscribe(subscription_id)
        except Exception as exc:
            raise TrueNASAPIError(
                f"could not unsubscribe from TrueNAS event {subscription_id!r}: {exc}"
            ) from exc
