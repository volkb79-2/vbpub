"""Explicit, serialized update acceptance using tnt plans and its guarded API client."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

import pytest

from tnt.actions import PlanExecutionError, apply_plan, make_plan
from tnt.errors import Refused, TransportError
from tnt.host_store import load_host_store
from tnt.profile import read_credential, resolve_paths
from tnt.session import open_session

pytestmark = pytest.mark.live

_PHASES = {"vanilla-v27", "patched-v27", "patched-v28"}
_DATASET_FIELDS = ("id", "name", "type")
_SNAPSHOT_FIELDS = ("id", "dataset", "name")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    assert value, f"live-update requires {name}"
    return value


def _version_for_system(target: str) -> str:
    return target if target.startswith("TrueNAS-") else f"TrueNAS-{target}"


def _target_is_family(target: str, family: int) -> bool:
    version = target[len("TrueNAS-"):] if target.startswith("TrueNAS-") else target
    return version.startswith(f"{family}.")


def _apply(session, action, confirmation: str | None = None, *, progress: list[dict] | None = None):
    plan = make_plan(action, session.rpc)
    print(plan.render(), flush=True)
    result = apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=confirmation,
                        job_progress=progress.append if progress is not None else None)
    return plan, result


def _open(profile_name: str, *, allow_write: bool):
    paths = resolve_paths()
    profiles, _, entries = load_host_store(paths, require_secrets=True)
    assert profile_name in profiles, f"profile {profile_name!r} is absent from {paths.config}"
    profile = profiles[profile_name]
    credential = read_credential(profile, entries)
    session = open_session(profile, secret=credential, allow_write=allow_write,
                           insecure_ack=os.environ.get("TNT_LIVE_INSECURE") == "1")
    return profile, credential, session


def _canonical_dump(session, dataset: str, snapshot: str) -> tuple[dict, str]:
    dataset_rows = session.rpc.call("pool.dataset.query", [["id", "=", dataset]],
                                    {"select": list(_DATASET_FIELDS)})
    if not isinstance(dataset_rows, list) or len(dataset_rows) != 1 or not isinstance(dataset_rows[0], dict):
        raise AssertionError(f"pool.dataset.query did not return exactly one row for {dataset!r}")
    dataset_row = {field: dataset_rows[0].get(field) for field in _DATASET_FIELDS}
    if (dataset_row["id"] != dataset or dataset_row["name"] != dataset
            or dataset_row["type"] != "FILESYSTEM"):
        raise AssertionError(f"dataset record lost its identity: {dataset_row!r}")
    snapshot_rows = session.rpc.call("pool.snapshot.query", [["id", "=", snapshot]],
                                     {"select": list(_SNAPSHOT_FIELDS)})
    if not isinstance(snapshot_rows, list) or len(snapshot_rows) != 1 or not isinstance(snapshot_rows[0], dict):
        raise AssertionError(f"pool.snapshot.query did not return exactly one row for {snapshot!r}")
    snapshot_row = {field: snapshot_rows[0].get(field) for field in _SNAPSHOT_FIELDS}
    if snapshot_row["id"] != snapshot or snapshot_row["dataset"] != dataset:
        raise AssertionError(f"snapshot record lost its identity: {snapshot_row!r}")
    document = {"dataset": dataset_row, "snapshot": snapshot_row}
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return document, hashlib.sha256(encoded).hexdigest()


def _wait_for_host(profile, credential, *, insecure_ack: bool, baseline: tuple[str, str, str], target: str):
    deadline = time.monotonic() + 1800
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if attempt > 1:
            time.sleep(10)
        fresh = None
        keep_session = False
        try:
            fresh = open_session(profile, secret=credential, allow_write=False, insecure_ack=insecure_ack)
            state = fresh.rpc.call("system.state")
            if state != "READY":
                print(f"[reconnect] attempt {attempt}: host state {state!r}", flush=True)
                continue
            try:
                report = fresh.workflows.verify_system_reboot(baseline, expected_version=target)
            except Refused as exc:
                if exc.rule == "system-reboot-not-observed":
                    print(f"[reconnect] attempt {attempt}: {exc}", flush=True)
                    continue
                raise
            keep_session = True
            return fresh, report
        except TransportError as exc:
            if exc.kind != "connect":
                raise
            print(f"[reconnect] attempt {attempt}: {exc}", flush=True)
        finally:
            if fresh is not None and not keep_session:
                fresh.close()
    raise AssertionError("host did not return with the expected version and a new boot ID within 30 minutes")


def _assert_job(result, method: str, progress: list[dict]) -> None:
    outcome = next((call.as_dict() for call in getattr(result, "calls", ()) if call.method == method), None)
    assert outcome is not None and outcome["status"] == "completed", f"{method} job did not complete: {outcome!r}"
    assert progress and any(isinstance(event, dict) for event in progress), f"{method} emitted no job progress"


def test_update_survival_for_selected_live_system():
    assert os.environ.get("TNT_LIVE") == "1", "selecting live-update requires TNT_LIVE=1"
    assert os.environ.get("TNT_LIVE_ALLOW_SYSTEM") == "1", "live updates require TNT_LIVE_ALLOW_SYSTEM=1"
    phase = _required("TNT_LIVE_UPDATE_PHASE")
    assert phase in _PHASES, f"TNT_LIVE_UPDATE_PHASE must be one of {sorted(_PHASES)}"
    profile_name = _required("TNT_LIVE_PROFILE")

    if phase == "patched-v28":
        profile, _, session = _open(profile_name, allow_write=False)
        try:
            expected = _required("TNT_LIVE_UPDATE_TARGET_VERSION")
            assert _target_is_family(expected, 28), f"patched-v28 must verify a v28 build, got {expected!r}"
            dataset = _required("TNT_LIVE_UPDATE_DATASET")
            snapshot = _required("TNT_LIVE_UPDATE_SNAPSHOT")
            expected_sha = _required("TNT_LIVE_UPDATE_CANONICAL_SHA256")
            assert len(expected_sha) == 64 and all(char in "0123456789abcdef" for char in expected_sha)
            assert session.backend.generation == "v28-verification-only", (
                "v28 verification must use the restricted read-only adapter"
            )
            before = session.workflows.system_status()
            assert before["version"] == _version_for_system(expected)
            assert before["state"] == "READY"
            _, observed_sha = _canonical_dump(session, dataset, snapshot)
            assert observed_sha == expected_sha, f"post-update canonical data SHA-256 differs: {observed_sha}"
            print(json.dumps({"phase": phase, "version": before["version"], "boot_id": before["boot_id"],
                              "active_boot_environment": before["active_boot_environment"], "dataset": dataset,
                              "snapshot": snapshot, "canonical_sha256": observed_sha, "survived": True},
                             sort_keys=True), flush=True)
        finally:
            session.close()
        return

    source = _required("TNT_LIVE_UPDATE_SOURCE_VERSION")
    target = _required("TNT_LIVE_UPDATE_TARGET_VERSION")
    target_profile = _required("TNT_LIVE_UPDATE_TARGET_PROFILE")
    target_train = _required("TNT_LIVE_UPDATE_TARGET_TRAIN")
    pool = _required("TNT_LIVE_POOL")
    assert pool != "boot-pool" and "/" not in pool, "TNT_LIVE_POOL must name one non-boot data pool"
    assert source.startswith("TrueNAS-27."), f"{phase} must start from v27, got {source!r}"
    assert ((phase == "vanilla-v27" and _target_is_family(target, 27)) or
            (phase == "patched-v27" and _target_is_family(target, 28))), (
            "vanilla-v27 must target a v27 nightly; patched-v27 must target v28"
        )

    profile, credential, session = _open(profile_name, allow_write=True)
    suffix = uuid.uuid4().hex[:12]
    dataset = f"{pool}/tnt-update-{suffix}"
    snapshot = f"{dataset}@tnt-update-{suffix}"
    try:
        baseline = session.workflows.system_update_baseline()
        assert baseline[0] == source, f"expected source version {source!r}, got {baseline[0]!r}"
        profiles = session.workflows.update_profiles()
        target_choice = next((row for row in profiles["profiles"] if row["profile"] == target_profile), None)
        assert target_choice is not None and target_choice["available"] is True, (
            f"target update profile {target_profile!r} is not reported as available"
        )
        if profiles["current_profile"] != target_profile:
            select_plan, _ = _apply(
                session, session.workflows.update_select_profile_action(target_profile), target_profile)
            assert select_plan.action.calls[0].method == "update.update"
            profiles = session.workflows.update_profiles()
            assert profiles["current_profile"] == target_profile
        versions = session.workflows.update_versions()
        assert {"train": target_train, "version": target} in versions, (
            f"TrueNAS does not list the exact requested target/train: {target!r} on {target_train!r}"
        )
        status = session.workflows.update_check()

        # Resolve all update/profile preconditions before writing a fixture so a
        # mismatch cannot leave a dataset behind on the disposable host.
        pools = session.rpc.call("pool.query", [], {})
        assert isinstance(pools, list) and any(row.get("name") == pool for row in pools if isinstance(row, dict)), (
            f"data pool {pool!r} is not visible through tnt"
        )
        try:
            session.workflows.dataset_create_action({"name": f"boot-pool/tnt-update-{suffix}", "type": "FILESYSTEM"})
        except Refused as exc:
            assert "boot-pool" in str(exc)
        else:
            raise AssertionError("tnt did not refuse a boot-pool update-survival fixture")

        existing = session.rpc.call("pool.dataset.query", [["id", "=", dataset]], {})
        assert existing == [], f"refusing to reuse pre-existing live fixture {dataset!r}"
        _apply(session, session.workflows.dataset_create_action({
            "name": dataset, "type": "FILESYSTEM",
        }))
        _apply(session, session.workflows.snapshot_create_action({"dataset": dataset, "name": f"tnt-update-{suffix}"}))
        canonical_before, digest_before = _canonical_dump(session, dataset, snapshot)
        print(json.dumps({"phase": phase, "source_version": source, "target_version": target,
                          "dataset": dataset, "snapshot": snapshot,
                          "selected_profile": profiles["current_profile"], "target_train": target_train,
                          "profile_candidate": status["new_version"],
                          "canonical_sha256": digest_before, "canonical_dump": canonical_before},
                         sort_keys=True), flush=True)

        download_progress: list[dict] = []
        download_plan, download_result = _apply(
            session, session.workflows.update_download_action(target, target_train=target_train), target,
            progress=download_progress,
        )
        assert download_plan.action.calls[0].method == "update.download"
        _assert_job(download_result, "update.download", download_progress)

        update_progress: list[dict] = []
        update_plan, update_result = _apply(
            session, session.workflows.update_apply_action(target, target_train=target_train), target,
            progress=update_progress,
        )
        assert update_plan.action.system_baseline == baseline
        assert update_plan.action.calls[0].method == "update.run"
        _assert_job(update_result, "update.run", update_progress)

        reboot_action = session.workflows.system_power_action("reboot")
        reboot_plan = make_plan(reboot_action, session.rpc)
        print(reboot_plan.render(), flush=True)
        try:
            apply_plan(reboot_plan, session.rpc, allow_write=True, typed_confirmation=reboot_action.confirm_target)
        except PlanExecutionError as exc:
            print(json.dumps({"reboot_submission": "connection-lost", "partial_calls": [
                call.as_dict() for call in exc.partial.calls]}, sort_keys=True), flush=True)
        session.close()
        session = None
        fresh, report = _wait_for_host(profile, credential, insecure_ack=os.environ.get("TNT_LIVE_INSECURE") == "1",
                                       baseline=reboot_action.system_baseline, target=target)
        try:
            expected_major = 28 if target.startswith("TrueNAS-28.") else 27
            assert fresh.backend.generation.startswith(f"v{expected_major}") or \
                expected_major == 28 and fresh.backend.generation == "v28-verification-only", (
                    f"unexpected post-update adapter: {fresh.backend.generation!r}"
                )
            canonical_after, digest_after = _canonical_dump(fresh, dataset, snapshot)
            assert canonical_after == canonical_before and digest_after == digest_before, (
                f"dataset/snapshot canonical records changed across update: {digest_after} != {digest_before}"
            )
            print(json.dumps({"phase": phase, "post_update": report, "dataset": dataset, "snapshot": snapshot,
                              "canonical_sha256": digest_after, "survived": True}, sort_keys=True), flush=True)
        finally:
            fresh.close()
    finally:
        if session is not None:
            session.close()
