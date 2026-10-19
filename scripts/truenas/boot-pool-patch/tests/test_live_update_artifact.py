from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tomllib

import pytest


TRUENAS_ROOT = Path(__file__).parents[2]
PATCH_ROOT = TRUENAS_ROOT / "boot-pool-patch"
ACCEPTANCE = PATCH_ROOT / "acceptance"
SCRIPT = ACCEPTANCE / "live_update.py"
METADATA = PATCH_ROOT / "v28-nightly-20261014" / "update-artifact.json"


def load_script():
    sys.path.insert(0, str(ACCEPTANCE))
    spec = importlib.util.spec_from_file_location("truenas_live_update", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_update_metadata_records_exact_nightly_and_update_size() -> None:
    runner = load_script()
    metadata = runner.load_update_metadata(METADATA)
    assert metadata["source_iso_sha256"] == "9312f77d0a94f54dc0396050bd26bb31ea0e81d62b124e30a94075a006e7bc9a"
    assert metadata["update_sha256"] == "96f0694c5541cc25d7c3b981943faf3f5b6bddfb28f72204e0370d185283a02c"
    assert metadata["version"] == "28.0.0-MASTER+20261014-230204"
    assert metadata["manifest_size_bytes"] == 10_025_572_761


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_iso_sha256", "not-a-digest"),
        ("update_sha256", "A" * 64),
        ("manifest_size_bytes", True),
        ("manifest_size_bytes", 0),
        ("update_member", "../TrueNAS.update"),
    ],
)
def test_update_metadata_rejects_invalid_or_ambiguous_values(tmp_path, field, value) -> None:
    runner = load_script()
    metadata = json.loads(METADATA.read_text())
    metadata[field] = value
    path = tmp_path / "update.json"
    path.write_text(json.dumps(metadata))
    with pytest.raises(runner.RemoteError):
        runner.load_update_metadata(path)


def test_update_lane_is_guarded_uses_readonly_payload_and_reports_marker_survival() -> None:
    source = SCRIPT.read_text()
    assert "TRUENAS_TEST_ALLOW_MUTATIONS" in source
    assert "TRUENAS_TEST_ALLOW_UPDATE" in source
    assert "TRUENAS_TEST_ALLOW_REBOOT" in source
    assert 'target.call("update.manual", UPDATE_PATH, {"cleanup": False}' in source
    assert "blockdev --getro" in source
    assert "wait_for_update_job" in source
    assert "marker did not survive update" in source
    assert "zpool status -x boot-pool" in source
    assert "TRUENAS_UPDATE_JOB_ID" in source
    assert "resuming existing update.manual job" in source
    assert "retrying in {delay}s" in source
    consumers = (PATCH_ROOT / "docs" / "CONSUMERS.md").read_text()
    design = (PATCH_ROOT / "docs" / "DESIGN-GUIDE.md").read_text()
    readme = (PATCH_ROOT / "README.md").read_text()
    assert "TRUENAS_UPDATE_PROOF_SHA256" in consumers
    assert "without launching a second update" in design
    assert "docs/CONSUMERS.md" in readme
    assert "resume read-only polling" in consumers


def test_resume_values_are_forwarded_to_the_registered_live_update_lane() -> None:
    config = tomllib.loads((TRUENAS_ROOT / "run-gate.toml").read_text())
    forwarded = set(config["environments"]["networked-tester"]["forward_env"])
    assert {
        "TRUENAS_UPDATE_JOB_ID",
        "TRUENAS_UPDATE_PROOF_DATASET",
        "TRUENAS_UPDATE_PROOF_SHA256",
    } <= forwarded


def test_resume_request_requires_job_and_matching_proof_values(monkeypatch) -> None:
    runner = load_script()
    names = (
        "TRUENAS_UPDATE_JOB_ID",
        "TRUENAS_UPDATE_PROOF_DATASET",
        "TRUENAS_UPDATE_PROOF_SHA256",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert runner.resume_request_from_environment() is None

    monkeypatch.setenv("TRUENAS_UPDATE_JOB_ID", "65")
    with pytest.raises(runner.RemoteError, match="requires .* together"):
        runner.resume_request_from_environment()

    monkeypatch.setenv("TRUENAS_UPDATE_JOB_ID", "65")
    monkeypatch.setenv(
        "TRUENAS_UPDATE_PROOF_DATASET",
        "boot-pool/user-data/update-proof-202610161707214f5c9eaa",
    )
    monkeypatch.setenv(
        "TRUENAS_UPDATE_PROOF_SHA256",
        "56584b92ba4180a23551f7eb81958e062ef6a40dcbd41d921283f76d3fa82bfb",
    )
    assert runner.resume_request_from_environment() == (
        65,
        "boot-pool/user-data/update-proof-202610161707214f5c9eaa",
        "56584b92ba4180a23551f7eb81958e062ef6a40dcbd41d921283f76d3fa82bfb",
    )

    monkeypatch.setenv("TRUENAS_UPDATE_PROOF_SHA256", "bad")
    with pytest.raises(runner.RemoteError, match="lowercase SHA-256"):
        runner.resume_request_from_environment()


def test_update_job_poll_retries_transient_ssh_banner_failure(monkeypatch) -> None:
    runner = load_script()

    class ScriptedTarget:
        calls = 0

        def call(self, _method, _filters, *, timeout):
            self.calls += 1
            if self.calls == 1:
                raise runner.RemoteError(
                    "remote command failed (255): midclt\\n"
                    "Connection timed out during banner exchange"
                )
            if self.calls == 2:
                return [{"state": "RUNNING", "progress": {"percent": 49, "description": "Extracting"}}]
            return [{"state": "SUCCESS", "progress": {"percent": 100, "description": "Complete"}}]

    target = ScriptedTarget()
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    completed = runner.wait_for_update_job(target, 65, timeout=10)
    assert completed["state"] == "SUCCESS"
    assert target.calls == 3
