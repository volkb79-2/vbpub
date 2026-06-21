"""Publish/approve/hold/rollback against the backend (spec §7).

The controller writes ONLY DATA to KV; it never pushes commands.
Rollback emits a NEW desired generation with action=rollback (never mutable
git reset / tag movement).

Wave barrier: after writing desired to a wave's nodes, poll their observed
state until all required nodes report healthy; only then advance.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import Dict, List, Optional, Set

from cmru.agent.consul_backend import ConsulBackend, ConsulUnavailable
from cmru.agent.protocol import ObservedState, DesiredState, ReleaseRef
from cmru.controller.planner import LandscapePlan, PlanStep

log = logging.getLogger(__name__)

# Poll interval when waiting for wave nodes to become healthy
_WAVE_POLL_INTERVAL = 15  # seconds
_WAVE_TIMEOUT = 1800      # 30 minutes max wait per wave


# ---------------------------------------------------------------------------
# Plan state persistence
# ---------------------------------------------------------------------------

_PLAN_STATUS_KEY = "cmru/controller/plans/{plan_id}/status"
_PLAN_APPROVAL_KEY = "cmru/controller/plans/{plan_id}/approved"
_PLAN_HOLD_KEY = "cmru/controller/plans/{plan_id}/hold"


def _plan_status_key(plan_id: str) -> str:
    return f"cmru/controller/plans/{plan_id}/status"


def _plan_approval_key(plan_id: str) -> str:
    return f"cmru/controller/plans/{plan_id}/approved"


def _plan_hold_key(plan_id: str) -> str:
    return f"cmru/controller/plans/{plan_id}/hold"


# ---------------------------------------------------------------------------
# Desired state construction
# ---------------------------------------------------------------------------

def _build_desired_json(
    step: PlanStep,
    generation: int,
    action: str = "update",
) -> str:
    """Build desired state JSON for a single node in a wave."""
    desired = {
        "schema_version": 1,
        "generation": generation,
        "action": action,
        "release": {
            "tag": step.release_tag,
            "manifest_url": step.manifest_url,
            "manifest_sha256": step.manifest_sha256,
        },
        "profiles": step.profiles,
        "config_hash": step.config_hash,
        "plan_id": step.plan_id,
        "step_id": step.step_id,
    }
    return json.dumps(desired)


# ---------------------------------------------------------------------------
# Rollout engine
# ---------------------------------------------------------------------------

class RolloutEngine:
    """Orchestrates the rollout of a LandscapePlan across waves.

    Uses the ConsulBackend to:
    - Write per-node desired state
    - Poll observed state to enforce wave barriers
    - Read/write plan status (approved, hold)
    """

    def __init__(
        self,
        backend: ConsulBackend,
        landscape: str,
        generation_base: int = 1,
        poll_interval: int = _WAVE_POLL_INTERVAL,
        wave_timeout: int = _WAVE_TIMEOUT,
        dry_run: bool = False,
    ) -> None:
        self._backend = backend
        self._landscape = landscape
        self._generation_base = generation_base
        self._poll_interval = poll_interval
        self._wave_timeout = wave_timeout
        self._dry_run = dry_run

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(self, plan: LandscapePlan) -> None:
        """Execute the plan: write desired state wave by wave, gate on wave barriers."""
        log.info("Publishing plan %s to landscape %s", plan.plan_id, self._landscape)

        for step in plan.steps:
            self._wait_for_approval_if_needed(plan.plan_id, step)
            self._check_hold(plan.plan_id)
            self._write_wave(step)
            if step.required:
                ok = self._wait_for_wave(plan.plan_id, step)
                if not ok:
                    log.error(
                        "Wave %s (phase %s) failed — stopping plan %s",
                        step.wave_name, step.phase, plan.plan_id,
                    )
                    self._write_plan_status(plan.plan_id, "failed", step.wave_name)
                    return

        self._write_plan_status(plan.plan_id, "complete", None)
        log.info("Plan %s complete", plan.plan_id)

    def approve(self, plan_id: str) -> None:
        """Approve production waves for the given plan."""
        if self._dry_run:
            log.info("[DRY RUN] Would approve plan %s", plan_id)
            return
        key = _plan_approval_key(plan_id)
        self._backend._put(f"/v1/kv/{key}", b"approved")
        log.info("Plan %s approved for production waves", plan_id)

    def hold(self, plan_id: str) -> None:
        """Pause the plan (write hold flag)."""
        if self._dry_run:
            log.info("[DRY RUN] Would hold plan %s", plan_id)
            return
        key = _plan_hold_key(plan_id)
        self._backend._put(f"/v1/kv/{key}", b"hold")
        log.info("Plan %s placed on hold", plan_id)

    def release_hold(self, plan_id: str) -> None:
        """Remove the hold flag."""
        if self._dry_run:
            log.info("[DRY RUN] Would release hold on plan %s", plan_id)
            return
        key = _plan_hold_key(plan_id)
        self._backend._delete(f"/v1/kv/{key}")
        log.info("Hold released for plan %s", plan_id)

    def rollback(
        self,
        plan: LandscapePlan,
        to_tag: Optional[str] = None,
        to_url: Optional[str] = None,
        to_sha256: Optional[str] = None,
        generation: Optional[int] = None,
    ) -> None:
        """Emit a new desired generation with action=rollback for all nodes in the plan.

        Never mutates git tags / existing desired generations — always writes a NEW
        generation with action='rollback'.  The to_* args specify the target release;
        if absent the first wave's release values are used (placeholder).
        """
        rollback_gen = generation or (self._generation_base + 10000)
        all_nodes: Set[str] = set()
        for step in plan.steps:
            all_nodes.update(step.nodes)

        # Use the plan's release for rollback target (caller provides --to override)
        first_step = plan.steps[0]
        rollback_tag = to_tag or first_step.release_tag
        rollback_url = to_url or first_step.manifest_url
        rollback_sha256 = to_sha256 or first_step.manifest_sha256

        rollback_step = PlanStep(
            plan_id=plan.plan_id,
            wave_name="rollback",
            phase=0,
            wave_type="canary",
            nodes=list(all_nodes),
            profiles=first_step.profiles,
            release_tag=rollback_tag,
            manifest_url=rollback_url,
            manifest_sha256=rollback_sha256,
            config_hash=first_step.config_hash,
            step_id=f"{plan.plan_id}.rollback",
            required=True,
            requires_approval=False,
        )

        log.info("Rollback: writing gen=%s action=rollback to %s nodes",
                 rollback_gen, len(all_nodes))
        self._write_wave(rollback_step, action="rollback", generation=rollback_gen)

    def status(self, plan: LandscapePlan) -> dict:
        """Return a summary of plan status + per-node observed state."""
        result: dict = {
            "plan_id": plan.plan_id,
            "landscape": self._landscape,
            "nodes": {},
        }
        all_nodes: Set[str] = set()
        for step in plan.steps:
            all_nodes.update(step.nodes)

        for node in sorted(all_nodes):
            obs_json = self._backend.read_observed(node, self._landscape)
            if obs_json:
                try:
                    obs = ObservedState.from_json(obs_json)
                    result["nodes"][node] = {
                        "health": obs.health,
                        "applied_generation": obs.applied_generation,
                        "adapter_phase": obs.adapter_phase,
                        "error_class": obs.error_class,
                    }
                except Exception:
                    result["nodes"][node] = {"health": "unknown", "raw": obs_json[:200]}
            else:
                result["nodes"][node] = {"health": "standby"}

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_wave(
        self,
        step: PlanStep,
        action: str = "update",
        generation: Optional[int] = None,
    ) -> None:
        """Write desired state for all nodes in the wave."""
        gen = generation if generation is not None else (
            self._generation_base + step.phase * 100
        )
        desired_json = _build_desired_json(step, gen, action)

        for node in step.nodes:
            key = f"cmru/landscapes/{self._landscape}/nodes/{node}/desired"
            if self._dry_run:
                log.info(
                    "[DRY RUN] Would write desired state gen=%s to node %s", gen, node
                )
            else:
                status, _ = self._backend._put(f"/v1/kv/{key}", desired_json.encode())
                if status not in (200, 201):
                    log.error("Failed to write desired state to %s: HTTP %s", node, status)
                else:
                    log.info("Wrote desired gen=%s to %s (wave=%s)", gen, node, step.wave_name)

    def _wait_for_wave(self, plan_id: str, step: PlanStep) -> bool:
        """Poll until all required nodes in the wave report healthy or timeout."""
        if self._dry_run:
            log.info("[DRY RUN] Would wait for wave %s", step.wave_name)
            return True

        expected_gen = self._generation_base + step.phase * 100
        required_nodes = set(step.nodes) if step.required else set()
        deadline = time.monotonic() + self._wave_timeout

        log.info(
            "Waiting for wave %s (phase %s): %s nodes, gen=%s",
            step.wave_name, step.phase, len(required_nodes), expected_gen,
        )

        while time.monotonic() < deadline:
            healthy: Set[str] = set()
            failed: Set[str] = set()

            for node in required_nodes:
                obs_json = self._backend.read_observed(node, self._landscape)
                if not obs_json:
                    continue
                try:
                    obs = ObservedState.from_json(obs_json)
                    if (
                        obs.applied_generation == expected_gen
                        and obs.health == "healthy"
                    ):
                        healthy.add(node)
                    elif obs.health == "failed" and obs.applied_generation == expected_gen:
                        failed.add(node)
                except Exception:
                    pass

            if failed:
                log.error(
                    "Wave %s: %s node(s) failed: %s — stopping plan",
                    step.wave_name, len(failed), sorted(failed),
                )
                return False

            if healthy == required_nodes:
                log.info("Wave %s: all %s required nodes healthy", step.wave_name, len(healthy))
                return True

            log.debug(
                "Wave %s: %s/%s healthy, waiting %ss …",
                step.wave_name, len(healthy), len(required_nodes), self._poll_interval,
            )
            time.sleep(self._poll_interval)

        log.error("Wave %s timed out after %ss", step.wave_name, self._wave_timeout)
        return False

    def _wait_for_approval_if_needed(self, plan_id: str, step: PlanStep) -> None:
        """Block until approval is granted for production waves."""
        if not step.requires_approval:
            return
        key = _plan_approval_key(plan_id)
        log.info(
            "Wave %s requires approval. Run: cmru-controller approve --plan %s",
            step.wave_name, plan_id,
        )
        while True:
            self._check_hold(plan_id)
            status, body, _ = self._backend._get(f"/v1/kv/{key}")
            if status == 200:
                log.info("Approval received for plan %s", plan_id)
                return
            log.debug("Waiting for approval for plan %s …", plan_id)
            time.sleep(self._poll_interval)

    def _check_hold(self, plan_id: str) -> None:
        """If a hold is set, block until it is released."""
        key = _plan_hold_key(plan_id)
        while True:
            status, _, _ = self._backend._get(f"/v1/kv/{key}")
            if status == 404:
                return  # no hold
            if status == 200:
                log.info("Plan %s is on hold — waiting for release …", plan_id)
                time.sleep(self._poll_interval)
            else:
                return  # unexpected — don't block

    def _write_plan_status(
        self,
        plan_id: str,
        status: str,
        failed_wave: Optional[str],
    ) -> None:
        payload = json.dumps({"status": status, "failed_wave": failed_wave}).encode()
        key = _plan_status_key(plan_id)
        self._backend._put(f"/v1/kv/{key}", payload)
