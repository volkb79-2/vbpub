"""Focused behavioral coverage for the final small CIU branch contracts."""
from __future__ import annotations

import importlib
import json
import runpy
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import activate, cli, diagnose, governance, provisioning, transport_ssh
from ciu.deploy_pkg import registry
from ciu.secrets.directives import parse_value


def test_module_entrypoint_calls_cli_main(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cli, "main", lambda: called.append(True))

    runpy.run_module("ciu.__main__", run_name="__main__")

    assert called == [True]


def test_importing_module_entrypoint_does_not_call_cli_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing CIU is side-effect-free; only ``python -m ciu`` invokes main."""
    called: list[bool] = []
    monkeypatch.delitem(sys.modules, "ciu.__main__", raising=False)
    monkeypatch.setattr(cli, "main", lambda: called.append(True))

    module = importlib.import_module("ciu.__main__")

    assert called == []
    assert module.main is cli.main
    # Keep the later runpy entrypoint test independent of this import test.
    sys.modules.pop("ciu.__main__", None)


def test_activation_without_remaining_arguments_has_no_trailing_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(activate, "ssh_exec", lambda _cfg, argv, **_kw: seen.append(argv) or 7)

    assert activate.run_activation(
        {"activate": "sh deploy/activate.sh"}, "apply",
        config={}, repo_root=tmp_path, bundle_dir="/opt/app",
    ) == 7
    assert seen == [["cd /opt/app && sh deploy/activate.sh apply"]]


def test_bake_without_no_cache_omits_flag_and_propagates_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: captured.append(argv) or 9)
    monkeypatch.setattr(sys, "argv", ["ciu", "bake", "api"])
    import ciu.engine as _engine
    monkeypatch.setattr(_engine, "get_git_hash", lambda: "abc12345")

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 9
    assert captured == [["docker", "buildx", "bake", "api", "--load", "--set", "*.labels.org.opencontainers.image.revision=abc12345"]]


def test_registry_ignores_scalar_auth_entry_but_honors_credential_helper(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "auths": {"registry.example.test": "not credentials"},
        "credHelpers": {"registry.example.test": "pass"},
    }), encoding="utf-8")

    assert registry.check_registry_auth("registry.example.test", config) is True


def test_running_exit_137_does_not_report_sigkill_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"Name": "/app", "State": {"Status": "running", "ExitCode": 137}}
    monkeypatch.setattr(diagnose, "_inspect", lambda _project: [item])

    assert all(finding.code != "exit_137" for finding in diagnose.collect())


def test_unhealthy_without_history_has_no_fabricated_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"Name": "/app", "State": {
        "Status": "running", "Health": {"Status": "unhealthy", "Log": []},
    }}
    monkeypatch.setattr(diagnose, "_inspect", lambda _project: [item])

    findings = diagnose.collect()
    unhealthy = next(finding for finding in findings if finding.code == "unhealthy")
    assert unhealthy.summary == "Healthcheck is unhealthy"


def test_pick_test_dir_falls_back_without_writing_var_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse_mkdir(_self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("controlled mkdir failure")

    monkeypatch.setattr(Path, "mkdir", refuse_mkdir)

    assert governance._pick_test_dir(tmp_path / "blocked" / "result.env") == Path("/var/tmp")


def test_pick_test_dir_falls_back_when_existing_parent_is_not_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(governance.os, "access", lambda *_args: False)

    assert governance._pick_test_dir(tmp_path / "result.env") == Path("/var/tmp")


def test_acyclic_graph_scans_subsequent_roots_without_cycle_error() -> None:
    stacks = {
        "a": {"requires": ["stack:b:healthy"], "provides": ["stack:a:healthy"]},
        "b": {"requires": [], "provides": ["stack:b:healthy"]},
        "c": {"requires": [], "provides": ["stack:c:healthy"]},
    }

    assert provisioning.lint_graph(stacks) == []


def test_normalized_cycle_is_reported_once() -> None:
    stacks = {
        "a": {"requires": ["stack:b:healthy"], "provides": ["stack:a:healthy"]},
        "b": {"requires": ["stack:a:healthy"], "provides": ["stack:b:healthy"]},
        "root": {"requires": ["stack:a:healthy"], "provides": ["stack:root:healthy"]},
    }

    cycle_errors = [error for error in provisioning.lint_graph(stacks) if "cycle" in error.lower()]
    assert len(cycle_errors) == 1


def test_gen_ephemeral_accepts_allowed_options_without_locator() -> None:
    spec = parse_value("nonce", {"directive": "GEN_EPHEMERAL", "mode": "0400"}, "app.secrets")

    assert spec.kind == "GEN_EPHEMERAL"
    assert spec.locator is None
    assert spec.mode == "0400"


def test_vault_key_with_newline_is_written_once_and_cleaned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    material = "-----BEGIN KEY-----\nvalue\n-----END KEY-----\n"
    monkeypatch.setattr("ciu.secrets.providers.resolve_vault_token", lambda *_: "token")
    monkeypatch.setattr("ciu.secrets.providers.vault_addr_from_config", lambda *_: "https://vault.invalid")
    monkeypatch.setattr(
        "ciu.secrets.providers.VaultKV2",
        lambda *_: SimpleNamespace(read=lambda *_args, **_kwargs: material),
    )

    path = transport_ssh.resolve_key(
        {"ssh_key": "ASK_VAULT:secret/data/key"}, {}, tmp_path,
    )
    try:
        assert Path(path).read_text(encoding="utf-8") == material
        assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600
    finally:
        Path(path).unlink()
    assert not Path(path).exists()
