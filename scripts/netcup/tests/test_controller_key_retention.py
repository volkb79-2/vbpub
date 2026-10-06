"""LT-KEY: the local controller key follows the on-host retention policy."""
from __future__ import annotations

import json
import shlex
import types

import pytest
from cli_extended import CliFailure

import case_harness
from case_harness import run_install_host

PUBKEY_MARKER = "{{CONTROLLER_SSH_PUBKEY}}"


def _script(retain, *, env_style: bool = False) -> str:
    """customScript in the exact shape debian-install-v2 build-customscript emits."""
    config = {"controller_ssh_pubkey": PUBKEY_MARKER, "swap_file_count": 8}
    parts = ["REPO_BRANCH=main"]
    if retain is not None and not env_style:
        config["retain_controller_ssh_key"] = retain
    if retain is not None and env_style:
        parts.append("RETAIN_CONTROLLER_SSH_KEY=" + ("yes" if retain else "no"))
    parts.append("VBPUB_CONFIG_EXTRA_JSON=" + shlex.quote(json.dumps(config, sort_keys=True, separators=(",", ":"))))
    parts += ["python3", "-c", shlex.quote("print('x')")]
    return " ".join(parts)


# --- parsing --------------------------------------------------------------

@pytest.mark.parametrize("retain", [True, False])
def test_reads_policy_from_bundle_json(install_host_mod, retain):
    assert install_host_mod._custom_script_host_key_retention(_script(retain)) is retain


@pytest.mark.parametrize("retain", [True, False])
def test_reads_policy_from_env_assignment(install_host_mod, retain):
    assert install_host_mod._custom_script_host_key_retention(_script(retain, env_style=True)) is retain


def test_json_wins_over_env_like_the_bootstrap_merge_order(install_host_mod):
    script = "RETAIN_CONTROLLER_SSH_KEY=yes " + _script(False)
    assert install_host_mod._custom_script_host_key_retention(script) is False


@pytest.mark.parametrize("script", [
    None, "", "echo hi", "VBPUB_CONFIG_EXTRA_JSON='{not json' python3 -",
    "unbalanced 'quote", _script(None),
    "RETAIN_CONTROLLER_SSH_KEY=maybe python3 -",
    "VBPUB_CONFIG_EXTRA_JSON='{\"retain_controller_ssh_key\":\"yes\"}' python3 -",
])
def test_unreadable_or_absent_policy_is_none_never_guessed(install_host_mod, script):
    assert install_host_mod._custom_script_host_key_retention(script) is None


# --- reconciliation ---------------------------------------------------------

def _args(local, explicit):
    return types.SimpleNamespace(local_controller_key=local, local_controller_key_explicit=explicit)


def test_default_local_follows_host_retain(install_host_mod, monkeypatch):
    monkeypatch.setattr(install_host_mod, "CONTROLLER_LOCAL_KEY_RETENTION", "remove")
    args = _args("remove", False)
    install_host_mod._reconcile_local_key_with_host(args, _script(True))
    assert args.local_controller_key == "retain"
    assert install_host_mod.CONTROLLER_LOCAL_KEY_RETENTION == "retain"
    assert args.host_controller_key == "retain"


def test_host_retain_with_explicit_local_remove_is_rejected(install_host_mod):
    args = _args("remove", True)
    with pytest.raises(CliFailure, match="host retain / local remove") as exc:
        install_host_mod._reconcile_local_key_with_host(args, _script(True))
    assert exc.value.exit_code == 2


def test_host_remove_leaves_local_choice_alone(install_host_mod):
    for local, explicit in (("remove", False), ("remove", True), ("retain", True)):
        args = _args(local, explicit)
        install_host_mod._reconcile_local_key_with_host(args, _script(False))
        assert args.local_controller_key == local and args.host_controller_key == "remove"


def test_undeclared_policy_does_not_touch_local_choice(install_host_mod):
    args = _args("remove", True)
    install_host_mod._reconcile_local_key_with_host(args, "echo hi")
    assert args.local_controller_key == "remove" and args.host_controller_key is None


def test_host_retain_with_local_retain_is_fine(install_host_mod):
    args = _args("retain", True)
    install_host_mod._reconcile_local_key_with_host(args, _script(True))
    assert args.local_controller_key == "retain"


# --- through the real main(): dry-run summary --------------------------------

def _dry_run(install_host_mod, tmp_path, monkeypatch, capsys, fake_client, script, extra=(), env=None):
    monkeypatch.setitem(case_harness.CONFIG_PAYLOAD, "customScript", script)
    original = install_host_mod._load_runtime_settings
    monkeypatch.setattr(
        install_host_mod, "_load_runtime_settings",
        lambda: {**original(), "ssh.controller_local_key_retention": "remove"},
    )
    return run_install_host(
        install_host_mod,
        ["install", "--dry-run", "--yes", "--ssh-identity-file", "controller-key", *extra],
        tmp_path=tmp_path, monkeypatch=monkeypatch, capsys=capsys, fake_client=fake_client,
        scenario="file", env=env,
    )


def test_dry_run_defaults_local_to_retain_and_prints_both(
    install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    run = _dry_run(install_host_mod, tmp_path, monkeypatch, capsys, fake_client, _script(True))
    assert run.status == 0
    assert "[dry-run] Local controller key after the completion marker: retain" in run.out
    assert "[dry-run] On-host controller key after the completion marker: retain" in run.out


def test_explicit_flag_remove_with_host_retain_is_refused_before_any_api_call(
    install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    run = _dry_run(install_host_mod, tmp_path, monkeypatch, capsys, fake_client, _script(True),
                   extra=("--local-controller-key", "remove"))
    assert run.status == 2
    assert "host retain / local remove" in run.err
    assert run.calls == []


def test_env_override_remove_with_host_retain_is_refused(
    install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    run = _dry_run(install_host_mod, tmp_path, monkeypatch, capsys, fake_client, _script(True),
                   env={"NETCUP_SCP_API_CONTROLLER_KEY_LOCAL_RETENTION": "remove"})
    assert run.status == 2 and run.calls == []


def test_dry_run_host_remove_keeps_settings_default(
    install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    run = _dry_run(install_host_mod, tmp_path, monkeypatch, capsys, fake_client, _script(False))
    assert run.status == 0
    assert "[dry-run] Local controller key after the completion marker: remove" in run.out
    assert "[dry-run] On-host controller key after the completion marker: remove" in run.out


def test_dry_run_undeclared_policy_is_reported_not_guessed(
    install_host_mod, tmp_path, monkeypatch, capsys, fake_client
):
    run = _dry_run(install_host_mod, tmp_path, monkeypatch, capsys, fake_client,
                   "CONTROLLER_SSH_PUBKEY='{{CONTROLLER_SSH_PUBKEY}}' python3 -")
    assert "[dry-run] Local controller key after the completion marker: remove" in run.out
    assert "On-host controller key after the completion marker: not declared by the customScript" in run.out
