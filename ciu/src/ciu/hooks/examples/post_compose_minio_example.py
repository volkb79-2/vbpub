#!/usr/bin/env python3
"""
Example post-compose hook: MinIO bucket initialisation.

This sample shows how to do the MinIO setup that was previously handled in the
CIU core (check_minio_ready / check_minio_bucket, removed from deploy.py).
Projects should copy and adapt this file — it is not imported by CIU itself.

What it covers
--------------
1. Detect the MinIO container name from the merged CIU ``config`` dict.
2. Wait for MinIO to be ready via ``mc ready local`` (run inside container).
3. Create an ``mc`` alias pointing at the local MinIO endpoint.
4. Create a project-scoped bucket if it does not already exist.
5. Optionally set a bucket-access policy.

Hook interfaces
--------------
CIU supports two calling conventions (both shown here):

* **Class-based** (preferred): ``class PostComposeHook`` with ``run(env)``
  and an optional ``requirements`` dict that is validated before ``run()``
  is called.
* **Function-based** (legacy): ``post_compose_hook(config, env) -> dict``

Both variants receive the *merged* CIU config dict and the current env dict.
Return an empty dict or a dict of env-var updates to persist.
"""
from __future__ import annotations

import subprocess
import time
from typing import Any, Dict


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _require(mapping: Dict[str, Any], key: str, label: str) -> Any:
    """Fetch *key* from *mapping*; raise ValueError with *label* if absent."""
    value = mapping.get(key)
    if value in (None, ""):
        raise ValueError(
            f"[minio post-hook] Required config key '{label}' is missing. "
            "Check that the TOML field is set and the stack was rendered correctly "
            "(run `ciu --print-context .` to inspect the merged config)."
        )
    return value


def _nested(config: Dict[str, Any], *path: str, label: str) -> Any:
    """Walk *config* along *path*; raise ValueError with *label* if any step fails."""
    cursor: Any = config
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            raise ValueError(f"[minio post-hook] Required config path '{label}' is missing.")
        cursor = cursor[key]
    if cursor in (None, ""):
        raise ValueError(f"[minio post-hook] Required config path '{label}' is empty.")
    return cursor


# ---------------------------------------------------------------------------
# Class-based hook (preferred style)
# ---------------------------------------------------------------------------


class PostComposeHook:
    """
    Class-based post-compose hook: initialise MinIO after ``docker compose up``.

    CIU validates ``requirements`` before calling ``run()``:

    * ``services``       – Docker container(s) that must be running.
    * ``config_sections`` – Top-level TOML sections that must exist in ``config``.

    Adapt these lists to match your stack's service names.
    """

    requirements: Dict[str, list] = {
        "services": ["minio"],          # container name fragment (exact match done inside run())
        "config_sections": ["deploy"],   # minimum required TOML sections
    }

    def __init__(self, env: dict | None = None) -> None:
        self.env = env or {}
        # Populated in run() once the merged config is available.
        self.project_name: str = ""
        self.environment: str = ""
        self.minio_container: str = ""
        self.minio_host: str = ""
        self.minio_port: str = ""
        self.minio_user: str = ""
        self.minio_password: str = ""

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _info(msg: str) -> None:
        print(f"[INFO]  [minio-hook] {msg}", flush=True)

    @staticmethod
    def _warn(msg: str) -> None:
        print(f"[WARN]  [minio-hook] {msg}", flush=True)

    @staticmethod
    def _error(msg: str) -> None:
        print(f"[ERROR] [minio-hook] {msg}", flush=True)

    # ------------------------------------------------------------------
    # MinIO operations (each runs a command inside the container via docker exec)
    # ------------------------------------------------------------------

    def _wait_for_minio(self, max_wait: int = 60) -> bool:
        """
        Poll ``mc ready local`` inside the MinIO container until it succeeds.

        ``mc`` is bundled in the official ``minio/minio`` image.
        Returns True when MinIO reports ready, False on timeout.
        """
        self._info(f"Waiting for MinIO to be ready (max {max_wait}s) …")
        for attempt in range(max_wait):
            try:
                result = subprocess.run(
                    ["docker", "exec", self.minio_container, "mc", "ready", "local"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    self._info(f"MinIO ready after {attempt + 1}s.")
                    return True
            except subprocess.TimeoutExpired:
                pass
            except Exception as exc:  # noqa: BLE001
                self._warn(f"mc ready check error: {exc}")
            time.sleep(1)
        self._error("Timed out waiting for MinIO.")
        return False

    def _configure_mc_alias(self) -> bool:
        """
        Register a local ``mc`` alias so subsequent ``mc`` commands can use
        ``local/<bucket>`` paths without repeating credentials.
        """
        self._info("Configuring mc alias …")
        result = subprocess.run(
            [
                "docker", "exec", self.minio_container,
                "mc", "alias", "set", "local",
                f"http://{self.minio_host}:{self.minio_port}",
                self.minio_user,
                self.minio_password,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            self._info("mc alias configured.")
            return True
        self._error(f"mc alias set failed: {result.stderr.strip()}")
        return False

    def _bucket_exists(self, bucket: str) -> bool:
        result = subprocess.run(
            ["docker", "exec", self.minio_container, "mc", "ls", f"local/{bucket}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0

    def _create_bucket(self, bucket: str) -> bool:
        """
        Create *bucket* if it does not already exist.
        MinIO bucket names must be lowercase.
        """
        if self._bucket_exists(bucket):
            self._info(f"Bucket already exists: {bucket}")
            return True
        self._info(f"Creating bucket: {bucket} …")
        result = subprocess.run(
            ["docker", "exec", self.minio_container, "mc", "mb", f"local/{bucket}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            self._info(f"Bucket created: {bucket}")
            return True
        self._error(f"mc mb failed: {result.stderr.strip()}")
        return False

    def _set_bucket_policy(self, bucket: str, policy: str = "none") -> None:
        """
        Set an anonymous access policy on *bucket*.

        Common values: ``none`` (private, default), ``download`` (public read),
        ``upload``, ``public`` (full public access).
        """
        self._info(f"Setting bucket policy '{policy}' on {bucket} …")
        result = subprocess.run(
            ["docker", "exec", self.minio_container, "mc", "anonymous", "set", policy, f"local/{bucket}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            self._warn(f"Could not set bucket policy (non-fatal): {result.stderr.strip()}")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, env: dict) -> dict:  # noqa: ARG002  (env unused in this example)
        """
        CIU calls this method with the current env dict after compose starts.

        ``env`` is intentionally unused here because all configuration is read
        from the merged ``config`` dict passed to the wrapper in ``PostComposeHook``
        below.  Adapt as needed if your project puts values in env instead.

        Returns an empty dict — this hook has no env-var updates to persist.
        """
        return {}

    def run_with_config(self, config: Dict[str, Any], env: dict) -> dict:  # noqa: ARG002
        """
        Extended entry point that receives the full merged CIU config.

        CIU's hook loader calls ``run(env)`` by default.  If you need the full
        config dict, rename this to ``run`` and adjust the signature accordingly.

        Typical pattern:
            deploy_cfg  = config.get('deploy', {})
            minio_cfg   = config.get('minio', {})    # adjust to your stack layout
        """
        # ---------- extract config ------------------------------------------
        deploy_cfg = config.get("deploy", {})
        self.project_name = _require(deploy_cfg, "project_name", "deploy.project_name")
        self.environment   = _require(deploy_cfg, "environment_tag", "deploy.environment_tag")

        # Adapt the section path to your stack (e.g. 'db_core.minio', 'infra.minio', …)
        minio_cfg = config.get("minio", {})
        minio_name = _require(minio_cfg, "name", "minio.name")

        self.minio_host     = minio_name  # service resolves via Docker network
        self.minio_port     = str(_require(minio_cfg, "internal_port", "minio.internal_port"))
        self.minio_user     = _require(minio_cfg, "root_user", "minio.root_user")

        # Secrets arrive as resolved values in the config dict after Vault resolution.
        secrets_cfg = config.get("secrets", {})
        self.minio_password = _require(secrets_cfg, "minio_root_password", "secrets.minio_root_password")

        # Container name follows the project naming convention: project-env-service
        self.minio_container = f"{self.project_name}-{self.environment}-{minio_name}"

        self._info(f"MinIO container : {self.minio_container}")
        self._info(f"MinIO endpoint  : http://{self.minio_host}:{self.minio_port}")

        # ---------- check container is running --------------------------------
        ps_result = subprocess.run(
            ["docker", "ps", "--filter", f"name={self.minio_container}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if self.minio_container not in ps_result.stdout:
            self._warn("MinIO container is not running — skipping bucket initialisation.")
            return {"status": "skipped", "reason": "container_not_running"}

        # ---------- wait → alias → bucket ------------------------------------
        if not self._wait_for_minio():
            return {"status": "error", "reason": "minio_not_ready"}
        if not self._configure_mc_alias():
            return {"status": "error", "reason": "mc_alias_failed"}

        # Bucket name: lowercase to satisfy MinIO naming rules
        bucket = f"{self.project_name}-{self.environment}".lower()
        bucket_ok = self._create_bucket(bucket)

        # Uncomment to make the bucket publicly readable:
        # if bucket_ok:
        #     self._set_bucket_policy(bucket, policy="download")

        if bucket_ok:
            self._info(f"✓ Bucket ready: {bucket}")
            return {"status": "success", "bucket": bucket}
        return {"status": "error", "reason": f"bucket_create_failed:{bucket}"}


# ---------------------------------------------------------------------------
# Function-based hook (legacy / simpler callers)
# ---------------------------------------------------------------------------


def post_compose_hook(config: dict, env: dict) -> dict:
    """
    Function-based variant — CIU also supports this simpler calling convention.

    Both function-based and class-based hooks receive the merged CIU config dict
    and the current env dict.  Return a dict of env-var updates to persist (or
    an empty dict if there is nothing to persist).

    This variant delegates to PostComposeHook.run_with_config() so the logic
    lives in one place.
    """
    hook = PostComposeHook(env=env)
    return hook.run_with_config(config, env)
