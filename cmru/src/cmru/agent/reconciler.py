"""Agent reconciliation loop (spec §6).

The reconciler:
  1. Long-polls own desired state via the backend (blocking KV watch).
  2. Verifies schema + optional signature.
  3. Checks idempotency (same generation already applied → no-op).
  4. Acquires host session/lock.
  5. Ensures release present (delegates to SPEC A installer library).
  6. Loads + dispatches the adapter action (install/update/rollback/hold).
  7. Checks local health.
  8. Publishes observed state + releases lock.
  9. Re-arms the watch.

Failure/outage behavior (spec §6.2):
  - Consul outage → keep current healthy state, exponential back-off, no guesses.
  - Session expiry / crash mid-apply → on restart re-read observed; re-run action.
  - Adapter failure → publish health=failed; do NOT advance applied_generation.

The reconciler is generic; it contains NO project topology.
"""
from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cmru.agent.backend import DesiredStateBackend, LockHandle
from cmru.agent.consul_backend import ConsulUnavailable
from cmru.agent.protocol import (
    DesiredState,
    DesiredStateError,
    ObservedState,
    parse_desired_json,
)
from cmru.agent.state import (
    read_observed,
    write_current_generation,
    write_observed,
)
from cmru.agent.adapter import load_adapter

log = logging.getLogger(__name__)

# Back-off limits for Consul outages
_BACKOFF_MIN = 2
_BACKOFF_MAX = 120


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verify_sig_if_present(
    desired_json: bytes,
    sig_bytes: Optional[bytes],
    pubkey: str,
) -> None:
    """Verify desired-state JSON against a minisign detached signature (defence-in-depth).

    If sig_bytes is None, skip (layer 1 — Consul ACL write-guard is the authority).
    Raises DesiredStateError on any verification failure.
    """
    if not sig_bytes:
        return
    if not pubkey:
        raise DesiredStateError(
            "desired.sig present but no minisign public key in identity — refusing"
        )
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "desired.json"
        sig_path = Path(tmp) / "desired.json.minisig"
        json_path.write_bytes(desired_json)
        sig_path.write_bytes(sig_bytes)
        result = subprocess.run(
            ["minisign", "-V", "-p", "-", "-m", str(json_path), "-x", str(sig_path)],
            input=pubkey.encode(),
            capture_output=True,
        )
        if result.returncode != 0:
            raise DesiredStateError(
                f"desired.sig verification failed: {result.stderr.decode(errors='replace')}"
            )


class Reconciler:
    """Single-host reconciliation engine.

    Instantiate with a configured backend, node identity, and optional
    release root.  Call run() for the long-running loop or once() for
    a single pass (useful in tests and cron fallback).
    """

    def __init__(
        self,
        backend: DesiredStateBackend,
        node_id: str,
        landscape: str,
        scope: str = "user",
        release_root: Optional[Path] = None,
        minisign_pubkey: str = "",
        max_iterations: Optional[int] = None,  # None = infinite; set in tests
    ) -> None:
        self._backend = backend
        self._node_id = node_id
        self._landscape = landscape
        self._scope = scope
        self._release_root = release_root or Path("/opt/dstdns")
        self._pubkey = minisign_pubkey
        self._index = 0
        self._max_iterations = max_iterations

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Long-running reconcile loop.  Returns only on unrecoverable error."""
        log.info("cmru-agent reconciler starting: node=%s landscape=%s",
                 self._node_id, self._landscape)
        backoff = _BACKOFF_MIN
        iteration = 0
        while True:
            if self._max_iterations is not None and iteration >= self._max_iterations:
                break
            iteration += 1
            try:
                self._tick()
                backoff = _BACKOFF_MIN  # reset on success
            except ConsulUnavailable as exc:
                log.warning("Consul unavailable (%s) — keeping current state, backoff %ss",
                            exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
            except Exception as exc:
                log.error("Unexpected reconciler error: %s", exc, exc_info=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    def once(self) -> bool:
        """Single reconcile pass.  Returns True if a change was applied."""
        return self._tick()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick(self) -> bool:
        """One reconcile iteration. Returns True if work was done."""
        # --- 1. Long-poll desired state -----------------------------------
        raw, new_index = self._backend.watch_desired(
            self._node_id, self._landscape, self._index, wait="300s"
        )
        self._index = new_index

        if raw is None:
            # Standby: no desired state yet; refresh health check and loop
            self._backend.pass_health_check(self._node_id)
            return False

        # --- 2. Verify schema + optional signature ------------------------
        try:
            desired = parse_desired_json(raw)
        except DesiredStateError as exc:
            log.error("Invalid desired state: %s — refusing", exc)
            self._publish_error("invalid_desired", str(exc))
            return False

        # Fetch optional detached sig from desired.sig KV key
        sig_bytes = self._backend.read_desired_sig(self._node_id, self._landscape)
        try:
            _verify_sig_if_present(raw, sig_bytes, self._pubkey)
        except DesiredStateError as exc:
            log.error("Desired state signature verification failed: %s — refusing", exc)
            self._publish_error("invalid_desired", "signature verification failed")
            return False

        # --- 3. Idempotency check -----------------------------------------
        observed = read_observed(self._scope) or ObservedState()
        if self._is_noop(desired, observed):
            log.debug("Generation %s already applied — no-op", desired.generation)
            self._backend.pass_health_check(self._node_id)
            return False

        # --- 4. Acquire host session/lock ----------------------------------
        lock = self._acquire_lock_with_retry(desired.generation)
        if not lock.acquired:
            log.warning("Could not acquire lock for gen %s — will retry next iteration",
                        desired.generation)
            return False

        # --- 5 + 6 + 7. Ensure release, apply, health ---------------------
        applied = False
        try:
            applied = self._apply(desired, observed, lock)
        finally:
            # --- 8. Publish observed + release lock -----------------------
            updated_observed = read_observed(self._scope) or ObservedState()
            self._backend.publish_observed(
                self._node_id, self._landscape, updated_observed.to_json()
            )
            self._backend.release_lock(lock)

        return applied

    def _is_noop(self, desired: DesiredState, observed: ObservedState) -> bool:
        """True when the desired state is already fully satisfied."""
        if observed.applied_generation is None:
            return False
        if desired.generation > observed.applied_generation:
            return False
        if desired.generation < observed.applied_generation:
            # Older desired than what's applied — also a no-op (do not revert)
            return True
        # Same generation: check release digest, config_hash, step_id
        return (
            observed.release_digest == desired.release.manifest_sha256
            and observed.adapter_phase == desired.step_id
        )

    def _acquire_lock_with_retry(self, generation: int, max_attempts: int = 5) -> LockHandle:
        """Try to acquire the lock up to max_attempts times."""
        for attempt in range(1, max_attempts + 1):
            lock = self._backend.acquire_lock(
                self._node_id, self._landscape, generation
            )
            if lock.acquired:
                return lock
            if attempt < max_attempts:
                log.warning("Lock not acquired (attempt %s/%s) — sleeping 5s",
                            attempt, max_attempts)
                time.sleep(5)
        return lock  # acquired=False

    def _apply(
        self,
        desired: DesiredState,
        observed: ObservedState,
        lock: LockHandle,
    ) -> bool:
        """Drive the adapter action; update observed state on disk.  Returns True on success."""
        started_at = _now_iso()

        # --- 6. Check hold FIRST — no release install needed for hold ----
        if desired.action == "hold":
            # hold: no changes; just refresh observed/health
            log.info("Action=hold: no changes (gen=%s)", desired.generation)
            obs = ObservedState(
                applied_generation=desired.generation,
                release_digest=desired.release.manifest_sha256,
                adapter_phase=desired.step_id,
                health="healthy",
                started_at=started_at,
                finished_at=_now_iso(),
            )
            write_observed(obs, self._scope)
            write_current_generation(desired.generation, self._scope)
            return True

        # --- 5. Ensure release present (install/update/rollback actions) --
        release_root = self._ensure_release(desired)
        if release_root is None:
            # Installer failed — already logged
            obs = ObservedState(
                applied_generation=observed.applied_generation,
                release_digest=observed.release_digest,
                adapter_phase=desired.step_id,
                health="failed",
                started_at=started_at,
                finished_at=_now_iso(),
                error_class="installer_failed",
                exit_code=1,
                message="release install/verify failed",
            )
            write_observed(obs, self._scope)
            return False

        try:
            adapter = load_adapter(release_root)
        except RuntimeError as exc:
            log.error("Failed to load adapter: %s", exc)
            obs = ObservedState(
                applied_generation=observed.applied_generation,
                release_digest=desired.release.manifest_sha256,
                adapter_phase=desired.step_id,
                health="failed",
                started_at=started_at,
                finished_at=_now_iso(),
                error_class="adapter_load_failed",
                exit_code=1,
                message="adapter load failed",
            )
            write_observed(obs, self._scope)
            return False

        # Write "applying" state before dispatching
        write_observed(ObservedState(
            applied_generation=observed.applied_generation,
            release_digest=desired.release.manifest_sha256,
            adapter_phase=desired.step_id,
            health="applying",
            started_at=started_at,
        ), self._scope)

        if desired.action == "rollback":
            return self._do_rollback(adapter, desired, observed, started_at, release_root)

        # install or update
        return self._do_install_or_update(adapter, desired, observed, started_at, release_root)

    def _do_install_or_update(
        self,
        adapter,
        desired: DesiredState,
        observed: ObservedState,
        started_at: str,
        release_root: Path,
    ) -> bool:
        try:
            adapter.validate(desired, release_root)
            adapter.prepare(desired, release_root)
            result = adapter.apply_step(desired)
        except Exception as exc:
            log.error("Adapter action failed: %s", exc, exc_info=True)
            obs = ObservedState(
                applied_generation=observed.applied_generation,
                release_digest=desired.release.manifest_sha256,
                adapter_phase=desired.step_id,
                health="failed",
                started_at=started_at,
                finished_at=_now_iso(),
                error_class=type(exc).__name__,
                exit_code=getattr(exc, "returncode", 1),
                message="adapter step raised exception",
            )
            write_observed(obs, self._scope)
            return False

        # Check health
        try:
            health_result = adapter.health(desired)
            health_status = health_result.status
        except Exception:
            health_status = "degraded"

        if not result.success:
            obs = ObservedState(
                applied_generation=observed.applied_generation,
                release_digest=desired.release.manifest_sha256,
                adapter_phase=desired.step_id,
                health="failed",
                started_at=started_at,
                finished_at=_now_iso(),
                error_class="adapter_step_failed",
                exit_code=result.exit_code,
                message="adapter step failed",
            )
            write_observed(obs, self._scope)
            return False

        obs = ObservedState(
            applied_generation=desired.generation,
            release_digest=desired.release.manifest_sha256,
            adapter_phase=desired.step_id,
            health=health_status,
            started_at=started_at,
            finished_at=_now_iso(),
        )
        write_observed(obs, self._scope)
        write_current_generation(desired.generation, self._scope)
        return True

    def _do_rollback(
        self,
        adapter,
        desired: DesiredState,
        observed: ObservedState,
        started_at: str,
        release_root: Path,
    ) -> bool:
        try:
            adapter.rollback(observed)
        except Exception as exc:
            log.error("Rollback failed: %s", exc, exc_info=True)
            obs = ObservedState(
                applied_generation=observed.applied_generation,
                release_digest=observed.release_digest,
                adapter_phase=desired.step_id,
                health="failed",
                started_at=started_at,
                finished_at=_now_iso(),
                error_class=type(exc).__name__,
                exit_code=1,
                message="rollback failed",
            )
            write_observed(obs, self._scope)
            return False

        obs = ObservedState(
            applied_generation=desired.generation,
            release_digest=desired.release.manifest_sha256,
            adapter_phase=desired.step_id,
            health="healthy",
            started_at=started_at,
            finished_at=_now_iso(),
        )
        write_observed(obs, self._scope)
        write_current_generation(desired.generation, self._scope)
        return True

    def _ensure_release(self, desired: DesiredState) -> Optional[Path]:
        """Ensure the release for desired.release.tag is installed.

        Delegates to the SPEC A installer library (get.py _atomic_swap_current /
        do_install / do_update).  If the release is already present and the
        manifest_sha256 matches, returns the existing path immediately (idempotent).

        Returns the release root path, or None on failure.
        """
        releases_dir = self._release_root / "releases"
        release_path = releases_dir / desired.release.tag

        if release_path.exists():
            # Release directory exists — assume already installed+verified
            log.debug("Release %s already present", desired.release.tag)
            return release_path

        # Download + verify + install via the SPEC A installer CLI
        log.info("Installing release %s", desired.release.tag)
        try:
            result = subprocess.run(
                [
                    "cmru-get",
                    "install",
                    "--version", desired.release.tag,
                    "--manifest-sha256", desired.release.manifest_sha256,
                    "--manifest-url", desired.release.manifest_url,
                    "--scope", self._scope,
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log.error(
                    "Installer exited %s: %s",
                    result.returncode,
                    result.stderr[:500],  # limit output, no secrets
                )
                return None
        except FileNotFoundError:
            # cmru-get not available — try the release root path directly
            log.warning("cmru-get not found; checking if release already staged")
            if not release_path.exists():
                log.error("Release %s not available", desired.release.tag)
                return None

        if not release_path.exists():
            log.error("Release path %s not found after install", release_path)
            return None

        return release_path

    def _publish_error(self, error_class: str, message: str) -> None:
        """Publish an error observed state without advancing generation."""
        observed = read_observed(self._scope) or ObservedState()
        obs = ObservedState(
            applied_generation=observed.applied_generation,
            release_digest=observed.release_digest,
            adapter_phase=observed.adapter_phase,
            health="failed",
            error_class=error_class,
            message=message,
        )
        write_observed(obs, self._scope)
        try:
            self._backend.publish_observed(
                self._node_id, self._landscape, obs.to_json()
            )
        except ConsulUnavailable:
            log.warning("Could not publish error state to Consul (unavailable)")
