"""Keep the parametrized live acceptance routes state-aware."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys


PATCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCH_ROOT / "acceptance"))
import accept


def test_path_a_and_b_scenario_runs_both_ui_and_api_profiles() -> None:
    assert accept._scenario_profiles("path-a+b") == ("path-a", "path-b")
    assert accept._scenario_profiles("path-a") == ("path-a",)
    assert accept._scenario_profiles("path-b") == ("path-b",)
    assert accept._scenario_profiles("vanilla") == ("path-a",)


def test_playwright_version_comes_from_pwmcp_contract_python_field(
    tmp_path: Path, monkeypatch,
) -> None:
    contract_path = tmp_path / "pwmcp" / "pwmcp.contract.json"
    contract_path.parent.mkdir()
    contract_path.write_text(
        json.dumps({"playwright": {"python": "1.62.0", "protocol": "1.62"}}),
        encoding="utf-8",
    )
    acceptance_path = tmp_path / "scripts" / "truenas" / "boot-pool-patch" / "acceptance"
    acceptance_path.mkdir(parents=True)
    (acceptance_path / "requirements-live-ui.txt").write_text("playwright==1.62.0\n", encoding="utf-8")
    monkeypatch.setattr(accept, "WORKTREE_ROOT", tmp_path)
    monkeypatch.setattr(accept, "ACCEPTANCE_ROOT", acceptance_path)

    assert accept._playwright_version() == "1.62.0"


def test_repository_live_ui_playwright_pin_matches_pwmcp_contract() -> None:
    contract = json.loads((accept.WORKTREE_ROOT / "pwmcp" / "pwmcp.contract.json").read_text())
    assert accept._playwright_version() == contract["playwright"]["python"]


def test_pwmcp_network_derivation_ignores_null_docker_aliases(monkeypatch) -> None:
    outputs = [
        subprocess.CompletedProcess([], 0, "other\npwmcp\n", ""),
        subprocess.CompletedProcess([], 0, json.dumps([{
            "Name": "/other",
            "NetworkSettings": {"Networks": {"test-bridge": {"Aliases": None}}},
        }]), ""),
        subprocess.CompletedProcess([], 0, json.dumps([{
            "Name": "/pwmcp",
            "NetworkSettings": {"Networks": {"test-bridge": {"Aliases": None}}},
        }]), ""),
    ]
    monkeypatch.delenv("TRUENAS_TEST_DOCKER_NETWORK", raising=False)
    monkeypatch.setattr(accept.subprocess, "run", lambda *args, **kwargs: outputs.pop(0))

    assert accept._pwmcp_network() == "test-bridge"


def _mock_snapshot_restore(monkeypatch, tmp_path: Path, dry_run_uuid: str) -> list[list[str]]:
    name = "tn-v27rc1-20g-stock-ssh-ready-20261031"
    calls: list[list[str]] = []
    monkeypatch.setattr(accept, "_host_lock", nullcontext)

    def provider_command(args, *, env, log_path):
        calls.append(args)
        if "--dry-run" in args:
            payload = {"wouldRevert": {"name": name, "uuid": dry_run_uuid}}
            log_path.write_text(json.dumps(payload), encoding="utf-8")
        elif "--yes" in args:
            log_path.write_text(
                json.dumps({"uuid": "11111111-1111-4111-8111-111111111111"}),
                encoding="utf-8",
            )
        elif args[0] == "server-details":
            log_path.write_text(
                json.dumps({"serverLiveInfo": {"state": "RUNNING"}}),
                encoding="utf-8",
            )
        return {"exit_code": 0}

    monkeypatch.setattr(accept, "_provider_command", provider_command)
    monkeypatch.setattr(
        accept, "_stream_command", lambda *args, **kwargs: {"exit_code": 0},
    )
    monkeypatch.setattr(accept, "_wait_for_ssh", lambda _env: None)
    return calls


def test_restore_snapshot_refuses_same_name_with_different_uuid(
    tmp_path: Path, monkeypatch,
) -> None:
    name = "tn-v27rc1-20g-stock-ssh-ready-20261031"
    calls = _mock_snapshot_restore(
        monkeypatch, tmp_path, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )

    try:
        accept._restore_snapshot(name, env={}, run_dir=tmp_path)
    except accept.AcceptError as exc:
        assert "UUID" in str(exc)
    else:
        raise AssertionError("restore accepted a same-name snapshot with a different UUID")

    assert len(calls) == 1
    assert "--dry-run" in calls[0]
    assert "--yes" not in calls[0]


def test_restore_snapshot_proceeds_when_dry_run_uuid_matches_catalog(
    tmp_path: Path, monkeypatch,
) -> None:
    name = "tn-v27rc1-20g-stock-ssh-ready-20261031"
    expected_uuid = accept.SNAPSHOT_CATALOG[name]["uuid"]
    calls = _mock_snapshot_restore(monkeypatch, tmp_path, expected_uuid)

    results = accept._restore_snapshot(name, env={}, run_dir=tmp_path)

    assert len(results) == 3
    assert any("--dry-run" in args for args in calls)
    assert any("--yes" in args for args in calls)


def test_v27_post_update_uses_digest_bound_reapply_for_existing_patch_record(
    tmp_path: Path, monkeypatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    seen = {}

    def lane_command(lane, *, env, **kwargs):
        seen.update(lane=lane, env=env, kwargs=kwargs)
        return {"lane": lane}

    monkeypatch.setattr(accept, "_lane_command", lane_command)
    result = accept._patch_after_update(
        "27.0.0-MASTER+20261029-173417", start_state="path-b",
        patched_target="reapply", source_record={"bundle_sha256": "a" * 64},
        manifest_path=manifest, base_env={}, run_dir=tmp_path,
        network="test-bridge", allow_high_psi=True,
    )

    assert result == {"lane": "live-v27-patch"}
    assert seen["env"]["TRUENAS_STORAGE_PROFILE"] == "path-b"
    assert seen["env"]["TRUENAS_V27_PATCH_ACTION"] == "reapply-after-update"
    assert seen["env"]["TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256"] == "a" * 64
    assert seen["env"]["TRUENAS_TEST_ALLOW_PATCH_REAPPLY"] == "1"


def test_v27_post_update_uses_guarded_install_when_existing_pool_has_no_stale_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        accept, "_lane_command",
        lambda lane, *, env, **kwargs: seen.update(lane=lane, env=env) or {"lane": lane},
    )

    accept._patch_after_update(
        "27.0.0-MASTER+20261029-173417", start_state="path-a+b",
        patched_target="reapply", source_record=None,
        manifest_path=manifest, base_env={}, run_dir=tmp_path,
        network="test-bridge", allow_high_psi=False,
    )

    assert seen["env"]["TRUENAS_STORAGE_PROFILE"] == "both"
    assert seen["env"]["TRUENAS_V27_PATCH_ACTION"] == "install-after-update"
    assert seen["env"]["TRUENAS_TEST_ALLOW_PATCH_REAPPLY"] == "1"
    assert "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256" not in seen["env"]


def test_v28_path_a_and_combined_sources_anchor_adapter_on_path_a(
    tmp_path: Path, monkeypatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        accept, "_lane_command",
        lambda lane, *, env, **kwargs: seen.append({"lane": lane, "env": env}) or {"lane": lane},
    )

    for start_state in ("path-a", "path-a+b"):
        seen.clear()
        result = accept._patch_after_update(
            "28.0.0-MASTER+20261029-175210", start_state=start_state,
            patched_target="reapply", source_record=None,
            manifest_path=manifest, base_env={}, run_dir=tmp_path,
            network="test-bridge", allow_high_psi=False,
        )

        assert result == {"lane": "live-v28-patch"}
        assert len(seen) == 1
        assert seen[0]["lane"] == "live-v28-patch"
        assert seen[0]["env"]["TRUENAS_STORAGE_PROFILE"] == "path-a"
        assert seen[0]["env"]["TRUENAS_V28_PATCH_ACTION"] == "install"


def test_v28_path_b_only_source_keeps_adapter_on_path_b(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        accept, "_lane_command",
        lambda lane, *, env, **kwargs: seen.update(lane=lane, env=env) or {"lane": lane},
    )

    accept._patch_after_update(
        "28.0.0-MASTER+20261029-175210", start_state="path-b",
        patched_target="reapply", source_record=None,
        manifest_path=manifest, base_env={}, run_dir=tmp_path,
        network="test-bridge", allow_high_psi=False,
    )

    assert seen["env"]["TRUENAS_STORAGE_PROFILE"] == "path-b"
    assert seen["env"]["TRUENAS_V28_PATCH_ACTION"] == "install"


def test_acceptance_index_reports_each_gate_and_keeps_latest_failure(tmp_path: Path) -> None:
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    record = {
        "schema_version": 1,
        "run_id": "run-1",
        "from_version": "27.0.0-RC.1",
        "start_state": "path-a",
        "to_version": "28.0.0-MASTER+20261029-175210",
        "patched_target": "reapply",
        "steps": ["plant", "update", "verify", "api", "ui"],
        "survival_state_directory": ".update-survival-test",
        "started_at_utc": "2026-10-30T12:00:00+00:00",
        "result_file": "results/run-1.json",
        "stage_results": [],
    }
    for index, lane in enumerate((
        "live-update-survival-plant", "live-update-survival-update",
        "live-update-survival-verify", "live-v28-patch", "live-storage", "live-ui",
    )):
        record["stage_results"].append({
            "argv": ["run-live-gate.py", lane],
            "completed_at": f"2026-10-30T12:0{index}:00+00:00",
            "exit_code": 1 if lane == "live-ui" else 0,
            "log": f"logs/{lane}-path-a.log" if lane in {"live-storage", "live-ui"}
                   else f"logs/{lane}.log",
        })
    (result_dir / "run-1.json").write_text(json.dumps(record), encoding="utf-8")

    index = accept._result_index(
        result_dir, ["28.0.0-MASTER+20261029-175210"],
    )

    assert "| path-a | 27.0.0-RC.1 | 28.0.0-MASTER+20261029-175210 | reapply | PASS | PASS | PASS | FAIL |" in index
    assert "tn-v27rc1-20g-path-b-patched-clean-20261031` | Retained but excluded" in index
