"""Guards on the destructive reinstall path that must each be killed by a test.

Adapted from the fix-5 reviewer's attack tests (digest binding, same-length
tamper, duplicate digest line, SSH key IDs, wrong bundle hash) and extended for
fix 6: snapshot of the bundle (N4), commit pin (N4), controller-key environment
(N3), the printed `--yes` digests, and the install-host wiring (T4).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from test_pre_reinstall_preflight import (
    PIN,
    _FakeProcess,
    _build_valid_inputs,
    _expected_payload,
    _fake_sent_script,
    _load_preflight,
    _successful_run_helpers,
    _wizard_summary,
)
from test_pre_reinstall_real_wizard import _real_wizard_dry_run

KEYS = (31415,)


def _setup(tmp_path):
    preflight, config, bundle = _build_valid_inputs(tmp_path)
    return preflight, config, bundle, _expected_payload(preflight, bundle)


def _check(preflight, output, payload, *, script=None, sent=None, keys=KEYS):
    script = payload["customScript"] if script is None else script
    preflight.validate_wizard_preview(
        "v1001", output,
        expected_custom_script=script,
        expected_sent_custom_script=_fake_sent_script(script) if sent is None else sent,
        expected_ssh_key_ids=list(keys),
    )


def _run(preflight, config, bundle, tmp_path, runner, popen, *, dry_run=False, **overrides):
    arguments = dict(
        disk_size="512GiB", root_start_sector=2_500_608, root_number=3,
        run_dir=tmp_path / "run", runner=runner, popen_factory=popen, dry_run=dry_run,
        ssh_key_ids=[31415], expected_bundle_sha256=preflight.sha256_file(bundle),
        expected_commit=PIN, commit_lookup=lambda branch: PIN,
    )
    arguments.update(overrides)
    return preflight.run_reinstall("v1001", config, bundle, **arguments)


# --- digest binding (T2) ----------------------------------------------------

def test_baseline_summary_is_accepted(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    _check(preflight, _wizard_summary(payload), payload)


def test_same_length_tamper_with_an_honest_digest_is_refused_by_the_digest_compare(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    script = payload["customScript"]
    tampered = script[:-1] + ("X" if script[-1] != "X" else "Y")
    assert len(tampered) == len(script)  # the length check cannot mask the digest check
    with pytest.raises(preflight.PreflightError, match="digest differs"):
        _check(preflight, _wizard_summary(dict(payload, customScript=tampered)), payload)


def test_a_duplicated_digest_line_is_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    output = _wizard_summary(payload)
    line = next(row for row in output.splitlines() if row.startswith("[payload] customScript sha256="))
    with pytest.raises(preflight.PreflightError, match="exactly one"):
        _check(preflight, output + line + "\n", payload)


def test_a_duplicated_sent_digest_line_is_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    output = _wizard_summary(payload)
    line = next(row for row in output.splitlines() if row.startswith("[payload] sent customScript"))
    with pytest.raises(preflight.PreflightError, match="exactly one"):
        _check(preflight, output + line + "\n", payload)


def test_an_extra_reviewed_digest_line_next_to_a_tampered_script_is_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    script = payload["customScript"]
    good = f"[payload] customScript sha256={hashlib.sha256(script.encode()).hexdigest()} len={len(script)}\n"
    with pytest.raises(preflight.PreflightError):
        _check(preflight, _wizard_summary(dict(payload, customScript=script + " ;curl x|sh")) + good, payload)


def test_sent_script_that_differs_from_the_reviewed_expansion_is_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    other = _fake_sent_script(payload["customScript"]).replace("AAAAfakekey", "AAAAstalekey")
    with pytest.raises(preflight.PreflightError, match="sent customScript"):
        _check(preflight, _wizard_summary(payload), payload, sent=other)


def test_missing_sent_digest_line_is_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    output = "\n".join(
        row for row in _wizard_summary(payload).splitlines() if not row.startswith("[payload] sent")
    )
    with pytest.raises(preflight.PreflightError, match="exactly one"):
        _check(preflight, output + "\n", payload)


def test_unredacted_summary_with_a_different_script_is_refused_by_direct_compare(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    output = "INSTALLATION PARAMETERS SUMMARY\n" + json.dumps(dict(payload, customScript="evil")) + "\n"
    with pytest.raises(preflight.PreflightError, match="payload mismatch"):
        _check(preflight, output, payload)


def test_redaction_marker_with_trailing_text_is_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    marker = f"***REDACTED customScript (len={len(payload['customScript'])})***"
    output = _wizard_summary(payload).replace(marker, marker + " ; curl x")
    with pytest.raises(preflight.PreflightError):
        _check(preflight, output, payload)


def test_wrong_or_extra_ssh_key_ids_are_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    with pytest.raises(preflight.PreflightError, match="payload mismatch"):
        _check(preflight, _wizard_summary(dict(payload, sshKeyIds=[31415, 999])), payload)
    with pytest.raises(preflight.PreflightError, match="payload mismatch"):
        _check(preflight, _wizard_summary(dict(payload, sshKeyIds=[999])), payload)


def test_first_summary_wins_and_a_missing_summary_is_refused(tmp_path):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    evil = "INSTALLATION PARAMETERS SUMMARY\n" + json.dumps(dict(payload, customScript="x")) + "\n"
    with pytest.raises(preflight.PreflightError):
        _check(preflight, evil + _wizard_summary(payload), payload)
    with pytest.raises(preflight.PreflightError):
        _check(preflight, "nothing", payload)


# --- reviewed hash and commit arguments (T1) --------------------------------

def test_yes_with_a_wrong_reviewed_bundle_hash_is_refused_before_any_remote_call(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen = _successful_run_helpers(preflight, bundle)
    calls = []
    with pytest.raises(preflight.PreflightError, match="differs from --expect-bundle-sha256"):
        _run(
            preflight, config, bundle, tmp_path,
            lambda *a, **k: calls.append(a), popen, expected_bundle_sha256="0" * 64,
        )
    assert calls == []


@pytest.mark.parametrize("bad", ["abc", "g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"])
def test_malformed_reviewed_bundle_hash_is_refused(tmp_path, bad):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen = _successful_run_helpers(preflight, bundle)
    with pytest.raises(preflight.PreflightError, match="64-character"):
        _run(preflight, config, bundle, tmp_path, runner, popen, expected_bundle_sha256=bad)


@pytest.mark.parametrize("bad", [None, "", "a" * 39, "g" * 40, "a" * 41, "main", "a" * 40 + "\n"])
def test_expect_commit_must_be_a_full_sha(tmp_path, bad):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen = _successful_run_helpers(preflight, bundle)
    with pytest.raises(preflight.PreflightError, match="full --expect-commit"):
        _run(preflight, config, bundle, tmp_path, runner, popen, expected_commit=bad)


def test_ssh_key_ids_are_required_positive_integers(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen = _successful_run_helpers(preflight, bundle)
    for bad in (None, [], [0], [-1], [True], ["3"]):
        with pytest.raises(preflight.PreflightError, match="--ssh-key-id"):
            _run(preflight, config, bundle, tmp_path, runner, popen, ssh_key_ids=bad)


def test_a_failing_dry_run_wizard_returns_its_status_without_payload_validation(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, base_popen = _successful_run_helpers(preflight, bundle)

    def popen(argv, **kwargs):
        process = base_popen(argv, **kwargs)
        process.stdout = __import__("io").StringIO("wizard exploded\n")
        process.returncode = 4
        return process

    assert _run(preflight, config, bundle, tmp_path, runner, popen, dry_run=True) == 4


def test_main_refuses_yes_without_the_reviewed_hash_before_running_anything(tmp_path, monkeypatch):
    preflight, config, bundle, _payload = _setup(tmp_path)
    monkeypatch.setattr(preflight, "run_reinstall", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as raised:
        preflight.main([
            "v1001", "--config", str(config), "--bundle", str(bundle), "--disk-size", "512GiB",
            "--root-start-sector", "2500608", "--root-number", "3", "--ssh-key-id", "31415",
            "--expect-commit", PIN, "--yes",
        ])
    assert raised.value.code == 2


def test_current_remote_commit_refuses_a_failed_ls_remote_even_with_plausible_output(monkeypatch):
    import types

    preflight = _load_preflight()
    monkeypatch.setattr(
        preflight.subprocess, "run",
        lambda argv, **kwargs: types.SimpleNamespace(
            returncode=128, stdout="b" * 40 + "\trefs/heads/main\n", stderr="fatal"
        ),
    )
    with pytest.raises(preflight.PreflightError, match="could not resolve"):
        preflight._current_remote_commit("main")


def test_task_state_file_is_private_even_when_it_already_exists(tmp_path):
    preflight = _load_preflight()
    state = tmp_path / "task-id"
    state.write_text("old\n", encoding="utf-8")
    os.chmod(state, 0o644)
    preflight._write_task_state(state, "01234567-89ab-cdef-0123-456789abcdef")
    assert (state.stat().st_mode & 0o777) == 0o600
    assert "task_id=01234567-89ab-cdef-0123-456789abcdef" in state.read_text(encoding="utf-8")


# --- commit pin (N4) --------------------------------------------------------

def test_the_customscript_pins_the_reviewed_commit(tmp_path):
    preflight, _config, bundle, payload = _setup(tmp_path)
    assert f"REPO_COMMIT={PIN}" in payload["customScript"]
    assert f"/vbpub/{PIN}/scripts/debian-install-v2/bootstrap-remote.py" in payload["customScript"]


def test_bundle_pinned_to_another_commit_is_refused_even_when_main_equals_the_reviewed_commit(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen = _successful_run_helpers(preflight, bundle)
    other = "b" * 40
    with pytest.raises(preflight.PreflightError, match="regenerated"):
        _run(
            preflight, config, bundle, tmp_path, runner, popen,
            expected_commit=other, commit_lookup=lambda branch: other,
        )


def test_unpinned_bundle_is_refused(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    with pytest.raises(preflight.PreflightError, match="regenerated"):
        preflight.real_validate_bundle(
            "v1001", config, bundle, disk_size="512GiB", root_start_sector=2_500_608,
            root_number=3, repo_commit=None,
        )
    assert preflight.real_validate_bundle(
        "v1001", config, bundle, disk_size="512GiB", root_start_sector=2_500_608,
        root_number=3, repo_commit=PIN,
    )


# --- bundle snapshot (N4) ---------------------------------------------------

def test_wizard_argv_pins_identity_ssh_target_server_image_and_key_retention(tmp_path):
    preflight = _load_preflight()
    host = preflight.HOSTS["v1001"]
    argv = preflight._wizard_argv("v1001", tmp_path / "b.json", tmp_path / "run", ssh_key_ids=[7], dry_run=False)

    def value(flag):
        return argv[argv.index(flag) + 1]

    assert value("--server-id") == str(host.server_id)
    assert value("--image") == str(host.image_id)
    assert value("--ssh-host") == host.ipv4
    assert value("--ssh-user") == "root"
    assert value("--ssh-identity-file") == str(host.ssh_key)
    assert value("--local-controller-key") == "retain"
    assert value("--custom-script-file") == str(tmp_path / "b.json")


def test_validation_reads_the_snapshot_not_the_operators_file(tmp_path, monkeypatch):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen = _successful_run_helpers(preflight, bundle)
    seen = []
    real = preflight.real_validate_bundle

    def spy(host, config_path, bundle_path, **kwargs):
        seen.append(Path(bundle_path))
        return real(host, config_path, bundle_path, **kwargs)

    monkeypatch.setattr(preflight, "validate_bundle", spy)
    assert _run(preflight, config, bundle, tmp_path, runner, popen) == 0
    assert seen == [tmp_path / "run" / "bundle.snapshot.json"]


def test_snapshot_and_manifest_are_private_and_name_the_snapshot_under_a_permissive_umask(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen = _successful_run_helpers(preflight, bundle)
    previous = os.umask(0o022)
    try:
        assert _run(preflight, config, bundle, tmp_path, runner, popen) == 0
    finally:
        os.umask(previous)
    snapshot = tmp_path / "run" / "bundle.snapshot.json"
    assert (snapshot.stat().st_mode & 0o777) == 0o600
    assert str(snapshot) in (tmp_path / "run" / "bundle.sha256").read_text(encoding="utf-8")


def test_both_wizard_runs_use_a_private_snapshot_of_the_validated_bytes(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    original = bundle.read_bytes()
    runner, base_popen = _successful_run_helpers(preflight, bundle)
    argvs = []

    def popen(argv, **kwargs):
        argvs.append(argv)
        # The operator's file changes after validation: it must not matter.
        bundle.write_bytes(b'{"customScript": "swapped after validation"}\n')
        return base_popen(argv, **kwargs)

    assert _run(
        preflight, config, bundle, tmp_path, runner, popen,
        expected_bundle_sha256=hashlib.sha256(original).hexdigest(),
    ) == 0
    snapshot = tmp_path / "run" / "bundle.snapshot.json"
    assert len(argvs) == 2
    for argv in argvs:
        assert argv[argv.index("--custom-script-file") + 1] == str(snapshot)
    assert snapshot.read_bytes() == original
    assert (snapshot.stat().st_mode & 0o777) == 0o600
    manifest = (tmp_path / "run" / "bundle.sha256").read_text(encoding="utf-8")
    assert manifest.startswith(hashlib.sha256(original).hexdigest())


def test_snapshot_changed_between_dry_run_and_submit_is_refused_and_never_submitted(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, base_popen = _successful_run_helpers(preflight, bundle)
    argvs = []

    def popen(argv, **kwargs):
        argvs.append(argv)
        process = base_popen(argv, **kwargs)
        if "--dry-run" in argv:
            snapshot = Path(argv[argv.index("--custom-script-file") + 1])
            snapshot.write_bytes(snapshot.read_bytes() + b" ")
        return process

    with pytest.raises(preflight.PreflightError, match="snapshot changed after the dry-run"):
        _run(preflight, config, bundle, tmp_path, runner, popen)
    assert len(argvs) == 1 and "--dry-run" in argvs[0]


# --- the --yes run's printed digests (N3/N4) --------------------------------

def _yes_popen(preflight, bundle, *, mutate):
    runner, base_popen = _successful_run_helpers(preflight, bundle)
    payload = _expected_payload(preflight, bundle)
    task = "Task UUID: 01234567-89ab-cdef-0123-456789abcdef\n"
    created = []

    def popen(argv, **kwargs):
        if "--dry-run" in argv:
            return base_popen(argv, **kwargs)
        process = _FakeProcess(mutate(payload, task))
        created.append(process)
        return process

    return runner, popen, created


def test_yes_run_printing_a_different_digest_is_stopped_and_refused(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    script = _expected_payload(preflight, bundle)["customScript"]
    runner, popen, created = _yes_popen(
        preflight, bundle,
        mutate=lambda payload, task: _wizard_summary(dict(payload, customScript=script + " ")).replace(
            "[dry-run] Preflight OK\n", ""
        ) + task,
    )
    with pytest.raises(preflight.PreflightError, match="differs from the reviewed one"):
        _run(preflight, config, bundle, tmp_path, runner, popen)
    assert created[0].terminated is True


def test_yes_run_with_only_the_loaded_digest_wrong_is_refused(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)

    def mutate(payload, task):
        text = _wizard_summary(payload).replace("[dry-run] Preflight OK\n", "")
        return re.sub(r"(\[payload\] customScript sha256=)[0-9a-f]{64}", r"\g<1>" + "0" * 64, text) + task

    runner, popen, created = _yes_popen(preflight, bundle, mutate=mutate)
    with pytest.raises(preflight.PreflightError, match="loaded customScript digest that differs"):
        _run(preflight, config, bundle, tmp_path, runner, popen)
    assert created[0].terminated is True


def test_yes_run_printing_a_digest_line_twice_is_refused(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)

    def mutate(payload, task):
        text = _wizard_summary(payload).replace("[dry-run] Preflight OK\n", "")
        line = next(row for row in text.splitlines() if row.startswith("[payload] customScript sha256="))
        return text + line + "\n" + task

    runner, popen, _created = _yes_popen(preflight, bundle, mutate=mutate)
    with pytest.raises(preflight.PreflightError, match="exactly one loaded and one sent"):
        _run(preflight, config, bundle, tmp_path, runner, popen)


def test_remote_identity_command_checks_every_identity_fact_and_stops_on_error():
    preflight = _load_preflight()
    command = preflight._remote_identity_command("v1001", 3)
    host = preflight.HOSTS["v1001"]
    assert f'test "$(hostname -f)" = {host.hostname}' in command
    assert f'test "$(hostnamectl --static)" = {host.short_name}' in command
    assert f'test "$(cat /etc/vbpub/server-id)" = {host.server_id}' in command
    assert 'test "$(lsblk -dn -o NAME /dev/vda)" = vda' in command
    assert "blockdev --getsize64 /dev/vda" in command and "lsblk -n -o START /dev/vda3" in command


def test_a_failing_identity_command_is_refused_even_if_it_printed_plausible_facts(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    popen_calls = []
    result = preflight.CommandResult(
        1, "v1001.vxxu.de\nv1001\n804027\nvda\n549755813888\n2500608\n", ""
    )
    with pytest.raises(preflight.PreflightError, match="host identity guard failed"):
        _run(
            preflight, config, bundle, tmp_path, lambda *a, **k: result,
            lambda *a, **k: popen_calls.append(a),
        )
    assert popen_calls == []


def test_yes_run_printing_a_different_sent_digest_is_stopped_and_refused(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)

    def mutate(payload, task):
        text = _wizard_summary(payload).replace("[dry-run] Preflight OK\n", "")
        return re.sub(
            r"(sent customScript \(placeholders expanded\) sha256=)[0-9a-f]{64}", r"\g<1>" + "0" * 64, text
        ) + task

    runner, popen, created = _yes_popen(preflight, bundle, mutate=mutate)
    with pytest.raises(preflight.PreflightError, match="sent customScript digest that differs"):
        _run(preflight, config, bundle, tmp_path, runner, popen)
    assert created[0].terminated is True


def test_yes_run_without_digest_lines_is_refused_with_a_may_have_been_submitted_warning(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, popen, _created = _yes_popen(
        preflight, bundle, mutate=lambda payload, task: "starting\n" + task
    )
    with pytest.raises(preflight.PreflightError, match="may already have been submitted"):
        _run(preflight, config, bundle, tmp_path, runner, popen)


def test_a_failing_yes_run_reports_its_exit_status_instead_of_a_digest_complaint(tmp_path):
    preflight, config, bundle, _payload = _setup(tmp_path)
    runner, base_popen = _successful_run_helpers(preflight, bundle)

    def popen(argv, **kwargs):
        process = base_popen(argv, **kwargs)
        if "--yes" in argv:
            process.stdout = __import__("io").StringIO("API refused\n")
            process.returncode = 3
        return process

    assert _run(preflight, config, bundle, tmp_path, runner, popen) == 3


# --- controller key environment (N3) ----------------------------------------

def test_wizard_child_does_not_inherit_a_stale_controller_key(tmp_path, monkeypatch):
    preflight = _load_preflight()
    monkeypatch.setenv("CONTROLLER_SSH_PUBKEY", "ssh-ed25519 AAAAstale operator-env")
    monkeypatch.setenv("KEEP_ME", "1")
    seen = {}

    def popen(argv, **kwargs):
        seen.update(kwargs)
        return _FakeProcess("done\n")

    with (tmp_path / "log").open("w", encoding="utf-8") as log:
        preflight._stream_wizard(
            ["python3", "install-host.py", "wizard", "--yes"], log=log,
            task_state_path=tmp_path / "task-id", popen_factory=popen,
        )
    assert "CONTROLLER_SSH_PUBKEY" not in seen["env"]
    assert seen["env"]["KEEP_ME"] == "1"


def test_expected_sent_script_uses_the_identity_key_and_ignores_the_environment(tmp_path, monkeypatch):
    preflight, _config, _bundle, payload = _setup(tmp_path)
    monkeypatch.setenv("CONTROLLER_SSH_PUBKEY", "ssh-ed25519 AAAAstale operator-env")
    sent = preflight.expected_sent_custom_script("v1001", payload["customScript"])
    assert "AAAAfakekey" in sent and "AAAAstale" not in sent
    assert "{{CONTROLLER_SSH_PUBKEY}}" not in sent


def test_expected_sent_script_quotes_single_quotes_like_the_wizard(tmp_path):
    preflight, _config, _bundle, _payload = _setup(tmp_path)
    preflight.read_controller_public_key = lambda _identity: "ssh-ed25519 AAAA it's"
    assert preflight.expected_sent_custom_script("v1001", "x {{CONTROLLER_SSH_PUBKEY}} y") == (
        "x ssh-ed25519 AAAA it'\"'\"'s y"
    )


@pytest.mark.parametrize("failure", ["empty", "raises"])
def test_expected_sent_script_refuses_when_no_key_can_be_derived(tmp_path, failure):
    preflight, _config, _bundle, _payload = _setup(tmp_path)

    def reader(_identity):
        if failure == "raises":
            raise FileNotFoundError("identity missing")
        return "  "

    preflight.read_controller_public_key = reader
    with pytest.raises(preflight.PreflightError, match="controller public key"):
        preflight.expected_sent_custom_script("v1001", "x {{CONTROLLER_SSH_PUBKEY}}")


# --- install-host wiring (T4) -----------------------------------------------

def test_wizard_config_inside_the_worktree_is_refused_end_to_end(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    from case_harness import run_install_host

    preflight, _config, bundle, _payload = _setup(tmp_path)
    argv = preflight._wizard_argv("v1001", bundle, tmp_path / "out", ssh_key_ids=[31415], dry_run=True)[2:]
    argv[argv.index("--server-id") + 1] = "42"
    argv[argv.index("--image") + 1] = "2"
    argv[argv.index("--ssh-identity-file") + 1] = "id_test"
    (tmp_path / "id_test").write_text("x\n")
    argv += ["--config", str(install_host_mod.WORKTREE_ROOT / "scripts/netcup/target-host.v1001.jsonc")]
    run = run_install_host(
        install_host_mod, argv, tmp_path=tmp_path, monkeypatch=monkeypatch, capsys=capsys,
        fake_client=fake_client, scenario="gather-id", create_inputs=False,
    )
    assert run.status == 2, run.err[-300:]
    assert "must be outside the worktree" in run.err + run.out


def test_wizard_config_outside_the_worktree_is_still_accepted(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    from case_harness import run_install_host

    preflight, _config, bundle, _payload = _setup(tmp_path)
    argv = preflight._wizard_argv("v1001", bundle, tmp_path / "out", ssh_key_ids=[31415], dry_run=True)[2:]
    argv[argv.index("--server-id") + 1] = "42"
    argv[argv.index("--image") + 1] = "2"
    argv[argv.index("--ssh-identity-file") + 1] = "id_test"
    (tmp_path / "id_test").write_text("x\n")
    argv += ["--config", str(tmp_path / "elsewhere" / "target-host.v1001.jsonc")]
    run = run_install_host(
        install_host_mod, argv, tmp_path=tmp_path, monkeypatch=monkeypatch, capsys=capsys,
        fake_client=fake_client, scenario="gather-id", create_inputs=False,
    )
    assert run.status in (0, None), run.err[-300:]
    assert "[payload] customScript sha256=" in run.out


def test_wizard_config_path_resolves_symlinks_into_the_worktree(tmp_path, install_host_mod):
    link = tmp_path / "innocent"
    link.symlink_to(install_host_mod.WORKTREE_ROOT)
    with pytest.raises(install_host_mod.CliFailure, match="outside the worktree"):
        # absolute() would keep the symlink spelling and accept this path
        install_host_mod._validate_wizard_config_output_path(link / "scripts/netcup/target-host.x.jsonc")
    assert install_host_mod._validate_wizard_config_output_path(tmp_path / "ok.jsonc") == tmp_path / "ok.jsonc"
    relative = os.path.relpath(install_host_mod.WORKTREE_ROOT / "x.jsonc", Path.cwd())
    with pytest.raises(install_host_mod.CliFailure, match="outside the worktree"):
        install_host_mod._validate_wizard_config_output_path(relative)


def test_install_summary_prints_the_loaded_and_sent_digest_lines(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    from case_harness import run_install_host

    run = run_install_host(
        install_host_mod, ["install", "--dry-run", "--yes"], tmp_path=tmp_path,
        monkeypatch=monkeypatch, capsys=capsys, fake_client=fake_client, scenario="file",
    )
    assert run.status == 0
    digest = hashlib.sha256(b"echo hi").hexdigest()
    assert f"[payload] customScript sha256={digest} len=7" in run.out
    assert f"[payload] sent customScript (placeholders expanded) sha256={digest} len=7" in run.out


def test_configure_summary_prints_the_loaded_and_sent_digest_lines(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    from case_harness import run_install_host

    run = run_install_host(
        install_host_mod,
        ["configure", "--dry-run", "--yes", "--no-monitor", "--image", "2", "--custom-script-file", "cs.sh"],
        tmp_path=tmp_path, monkeypatch=monkeypatch, capsys=capsys, fake_client=fake_client,
        scenario="gather",
    )
    assert run.status in (0, None), run.err[-300:]
    assert len(re.findall(r"(?m)^\[payload\] customScript sha256=[0-9a-f]{64} len=\d+$", run.out)) == 1
    assert len(re.findall(r"(?m)^\[payload\] sent customScript \(placeholders expanded\) sha256=", run.out)) == 1
