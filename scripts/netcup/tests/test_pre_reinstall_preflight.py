"""Offline and fake-command coverage for the destructive reinstall preflight."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


NETCUP_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = NETCUP_DIR / "pre-reinstall.py"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("netcup_pre_reinstall_preflight", PREFLIGHT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_valid_inputs(tmp_path: Path, *, host="v1001"):
    preflight = _load_preflight()
    package_root = NETCUP_DIR.parent / "debian-install-v2"
    import sys

    sys.path.insert(0, str(package_root))
    try:
        from debian_install_v2.config import load_config
        from debian_install_v2.customscript import build_customscript_bundle
    finally:
        sys.path.remove(str(package_root))

    config_path = NETCUP_DIR / "examples" / "debian-install-v2" / f"{host}.json"
    config = load_config(str(config_path))
    bundle = build_customscript_bundle(
        config, repo_branch="main", controller_ssh_placeholder=True
    )
    bundle_path = tmp_path / f"{host}-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return preflight, config_path, bundle_path


def test_preflight_validates_config_bundle_offline_plan_and_sha256(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    checked = preflight.validate_bundle(
        "v1001", config_path, bundle_path,
        disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        repo_branch="main",
    )
    assert checked.digest == preflight.sha256_file(bundle_path)
    assert checked.plan["plan_table"][0]["start"] == 2_500_608
    assert [row["number"] for row in checked.plan["plan_table"]] == list(range(3, 10))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("root_max_size_gb", 40),
        ("root_resize_policy", "grow"),
        ("disk_layout", "dev-host"),
    ],
)
def test_preflight_refuses_stale_payload_bundle_before_submit(tmp_path, key, value):
    preflight, config_path, _bundle_path = _build_valid_inputs(tmp_path)
    stale = json.loads(config_path.read_text(encoding="utf-8"))
    stale[key] = value
    stale_config_path = tmp_path / "stale.json"
    stale_config_path.write_text(json.dumps(stale), encoding="utf-8")
    stale_bundle_path = tmp_path / "stale-bundle.json"
    stale_bundle_path.write_text(json.dumps({
        "config": stale,
        "customScript": "REPO_BRANCH=main VBPUB_CONFIG_EXTRA_JSON='" + json.dumps(stale) + "' python3 -c pass",
        "completionMarker": "/var/lib/vbpub/bootstrap/stage2_done",
    }), encoding="utf-8")

    calls = []
    run_dir = tmp_path / "stale-run"
    with pytest.raises(preflight.PreflightError, match="strict v2 config validation"):
        preflight.run_reinstall(
            "v1001", stale_config_path, stale_bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
            repo_branch="main", run_dir=run_dir,
            runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
    assert calls == []
    log = (run_dir / "reinstall.log").read_text(encoding="utf-8")
    assert "strict v2 config validation" in log
    assert "EXIT=2" in log
    assert (run_dir / "reinstall.log.EXIT").read_text(encoding="utf-8").strip() == "2"


@pytest.mark.parametrize("host", ["r1002", "v1001"])
def test_identity_guards_check_api_and_ssh_host_facts(host):
    preflight = _load_preflight()
    expected = preflight.HOSTS[host]
    record = {
        "id": expected.server_id,
        "name": expected.server_name,
        "hostname": expected.hostname,
        "ipv4Addresses": [{"ip": expected.ipv4}],
        "serverLiveInfo": {"state": "RUNNING", "disks": [{"dev": "vda"}]},
    }
    assert preflight.validate_api_record(host, record) is None
    assert preflight.validate_ssh_identity(
        host,
        f"{expected.hostname}\n{expected.short_name}\n{expected.server_id}\nvda\n",
    ) is None
    command = preflight._remote_identity_command(host, 3)
    assert "set -e" in command
    api_check = "r1002_api_check" if host == "r1002" else "v1001_api_check"
    ssh_function = "r1002_ssh" if host == "r1002" else "v1001_ssh"
    assert command.index(api_check) < command.index(ssh_function)

    for field, wrong in (
        ("hostname", "wrong.example"),
        ("name", "wrong-server"),
        ("id", expected.server_id + 1),
    ):
        bad = dict(record)
        bad[field] = wrong
        with pytest.raises(preflight.PreflightError, match="API identity mismatch"):
            preflight.validate_api_record(host, bad)
    with pytest.raises(preflight.PreflightError, match="SSH identity mismatch"):
        preflight.validate_ssh_identity(host, "wrong.example\nwrong\n1\nvda\n")


def test_provider_payload_guard_checks_target_image_disk_and_customscript():
    preflight = _load_preflight()
    expected = preflight.HOSTS["v1001"]
    payload = {
        "serverId": expected.server_id,
        "hostname": expected.hostname,
        "imageFlavourId": expected.image_id,
        "diskName": "vda",
        "customScript": "reviewed customScript",
    }
    assert preflight.validate_provider_payload("v1001", payload, expected_custom_script="reviewed customScript") is None
    for key, value in (
        ("serverId", expected.server_id + 1),
        ("hostname", "wrong.example"),
        ("imageFlavourId", expected.image_id + 1),
        ("diskName", "vdb"),
        ("customScript", "stale script"),
    ):
        bad = dict(payload)
        bad[key] = value
        with pytest.raises(preflight.PreflightError, match="provider payload mismatch"):
            preflight.validate_provider_payload("v1001", bad, expected_custom_script="reviewed customScript")


@pytest.mark.parametrize("status", [0, 17])
def test_run_log_always_records_exit_status(tmp_path, status):
    preflight = _load_preflight()
    log_path = tmp_path / "v1001" / "reinstall.log"
    exit_path = preflight.run_logged(log_path, lambda: status)
    assert exit_path == status
    assert f"EXIT={status}" in log_path.read_text(encoding="utf-8")
    assert (log_path.with_suffix(log_path.suffix + ".EXIT")).read_text(encoding="utf-8").strip() == str(status)


def test_run_guard_order_and_image_pin_are_explicit_in_submitted_argv(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        if len(seen) == 1:
            command = argv[-1]
            assert command.index("v1001_api_check") < command.index("v1001_ssh")
            return preflight.CommandResult(
                0,
                "v1001.vxxu.de\nv1001\n804027\nvda\n549755813888\n2500608\n",
                "",
            )
        assert "--image" in argv and argv[argv.index("--image") + 1] == "128"
        assert "--out-dir" in argv
        return preflight.CommandResult(0, "wizard finished", "")

    result = preflight.run_reinstall(
        "v1001", config_path, bundle_path,
        disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        run_dir=tmp_path / "run", runner=fake_run,
    )
    assert result == 0
    assert len(seen) == 2
    log = (tmp_path / "run" / "reinstall.log").read_text(encoding="utf-8")
    assert "START=" in log and "EXIT=0" in log
    assert (tmp_path / "run" / "bundle.sha256").is_file()


def test_preflight_dry_run_records_plan_and_never_submits_consent(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        if len(seen) == 1:
            return preflight.CommandResult(
                0,
                "v1001.vxxu.de\nv1001\n804027\nvda\n549755813888\n2500608\n",
                "",
            )
        assert "--dry-run" in argv
        assert "--yes" not in argv
        return preflight.CommandResult(0, "[dry-run] Preflight OK\n", "")

    result = preflight.run_reinstall(
        "v1001", config_path, bundle_path,
        disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        run_dir=tmp_path / "preview", dry_run=True, runner=fake_run,
    )
    assert result == 0
    assert len(seen) == 2
    log = (tmp_path / "preview" / "reinstall.log").read_text(encoding="utf-8")
    assert "[dry-run] Preflight OK" in log
    assert "EXIT=0" in log
    assert (tmp_path / "preview" / "bundle.sha256").is_file()


def test_ssh_identity_refusal_never_runs_wizard(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return preflight.CommandResult(
            0,
            "wrong.example\nwrong\n0\nvda\n549755813888\n2500608\n",
            "",
        )

    with pytest.raises(preflight.PreflightError, match="SSH identity mismatch"):
        preflight.run_reinstall(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
            run_dir=tmp_path / "run", runner=fake_run,
        )
    assert len(calls) == 1
    assert "--yes" not in calls[0]
