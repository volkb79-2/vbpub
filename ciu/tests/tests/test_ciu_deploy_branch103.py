"""Behavioral coverage for the remaining deploy orchestration decisions."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu import engine  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _profile(phases: dict | None = None, **extra: dict) -> Profile:
    config = {
        "deploy": {
            "project_name": "ciu",
            "environment_tag": "test",
            "phases": phases or {},
        }
    }
    config.update(extra)
    return Profile(name=None, phase_keys=None, config=config)


def _entry(path: str, phase_num: int, *, health: bool = True) -> dict:
    name = Path(path).name
    return {
        "phase_num": phase_num,
        "phase_key": f"phase_{phase_num}",
        "path": path,
        "name": name,
        "service": {"path": path, "name": name, "enabled": True, "health": health},
    }


def test_unsupported_duration_value_uses_default_and_warns(capsys: pytest.CaptureFixture[str]) -> None:
    assert deploy._seconds(object(), default=17.0) == 17.0
    warning = capsys.readouterr().out
    assert "could not parse duration" in warning
    assert "using 17s" in warning


def test_git_hash_reports_clean_and_dirty_repository_state(monkeypatch) -> None:
    """Version metadata exposes the short revision and honest dirty suffix."""
    responses = iter((
        SimpleNamespace(stdout="abc12345\n"),
        SimpleNamespace(stdout="\n"),
        SimpleNamespace(stdout="abc12345\n"),
        SimpleNamespace(stdout=" M ciu/src/ciu/deploy.py\n"),
    ))
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *_args, **_kwargs: next(responses))

    assert engine.get_git_hash() == "abc12345"
    assert engine.get_git_hash() == "abc12345-dirty"


def test_bake_revision_args_stamp_the_label_and_carry_the_dirty_suffix(monkeypatch) -> None:
    """Provenance: a baked image names the commit it came from.

    The `-dirty` suffix must survive into the label. A dirty build that stamped
    a clean commit would be a MORE convincing version of the "which artifact did
    this result test?" problem, not a fix for it.
    """
    monkeypatch.setattr(engine, "get_git_hash", lambda: "abc12345")
    assert engine.bake_revision_args() == [
        "--set", "*.labels.org.opencontainers.image.revision=abc12345",
    ]

    monkeypatch.setattr(engine, "get_git_hash", lambda: "abc12345-dirty")
    assert engine.bake_revision_args() == [
        "--set", "*.labels.org.opencontainers.image.revision=abc12345-dirty",
    ]


def test_bake_revision_args_stamp_nothing_when_the_revision_is_unknown(monkeypatch) -> None:
    """No git (get_git_hash() -> "dev") must stamp NOTHING, not `revision=dev`.

    A label reading "dev" looks like an answer and would be trusted as one; an
    absent label is honestly absent. Also keeps a non-git checkout baking with a
    byte-identical command to before this feature existed.
    """
    monkeypatch.setattr(engine, "get_git_hash", lambda: "dev")
    assert engine.bake_revision_args() == []


def test_vault_preflight_keeps_earliest_secret_phase(monkeypatch, tmp_path: Path) -> None:
    profile = _profile(
        {"phase_2": {"services": []}},
        vault={"stack_path": "infra/vault"},
    )
    selection = [
        _entry("applications/later", 3),
        _entry("applications/earlier", 2),
        _entry("infra/vault", 2),
    ]
    rendered = {
        "applications/later": {"app": {"secrets": {"password": "ASK_VAULT:secret/later"}}},
        "applications/earlier": {"app": {"secrets": {"password": "ASK_VAULT:secret/earlier"}}},
        "infra/vault": {"vault_core": {}},
    }
    monkeypatch.setattr(deploy, "resolve_vault_token", lambda *_args: None)
    monkeypatch.setattr(deploy, "vault_addr_from_config", lambda _config: "http://vault:8200")

    with pytest.raises(ValueError, match=r"\[S7\.6\]"):
        deploy.vault_preflight(tmp_path, profile, selection, rendered)


def test_non_vault_stack_does_not_satisfy_vault_ordering(monkeypatch, tmp_path: Path) -> None:
    profile = _profile(vault={"stack_path": "infra/vault"})
    selection = [_entry("infra/not-vault", 1), _entry("applications/app", 2)]
    rendered = {
        "infra/not-vault": {"network_core": {}},
        "applications/app": {"app": {"secrets": {"password": "ASK_VAULT:secret/app"}}},
    }
    monkeypatch.setattr(deploy, "resolve_vault_token", lambda *_args: None)
    monkeypatch.setattr(deploy, "vault_addr_from_config", lambda _config: "http://vault:8200")

    with pytest.raises(ValueError, match=r"\[S7\.6\]"):
        deploy.vault_preflight(tmp_path, profile, selection, rendered)


def test_health_resolution_deduplicates_container_names(tmp_path: Path) -> None:
    for path in ("applications/api", "tools/api-copy"):
        stack = tmp_path / path
        stack.mkdir(parents=True)
        (stack / "ciu.compose.yml").write_text(
            "services:\n  api:\n    container_name: ciu-test-api\n", encoding="utf-8"
        )

    profile = _profile()
    assert deploy.resolve_selection_health_containers(
        tmp_path, profile, [_entry("applications/api", 1), _entry("tools/api-copy", 2)],
        default_timeout_s=30.0,
    ) == {"ciu-test-api": 30.0}


def test_deploy_ignore_errors_records_preflight_failure_and_continues(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = _profile(
        {
            "phase_1": {"services": [{"path": "infra/first", "enabled": True}]},
            "phase_2": {"services": [{"path": "applications/later", "enabled": True}]},
        }
    )
    selection = deploy.build_selection(profile)
    probed: list[list[str]] = []
    started: list[str] = []

    def fake_preflight(_root, _profile, entries, _rendered, **_kwargs):
        paths = [entry["path"] for entry in entries]
        probed.append(paths)
        if paths == ["infra/first"]:
            raise ValueError("preflight failed")

    monkeypatch.setattr(deploy, "provisioning_preflight", fake_preflight)
    monkeypatch.setattr(deploy, "_run_stack", lambda stack_dir, **_kwargs: started.append(stack_dir.name) or True)

    assert deploy.action_deploy(
        tmp_path, profile, selection, dry_run=False, ignore_errors=True,
        health_after_phase=False, update_cert_permission=False, rendered={},
    ) == 1
    assert probed == [["infra/first"], ["applications/later"]]
    assert started == ["later"]
    assert "infra/first" in capsys.readouterr().out


def test_health_failure_reclassifies_started_stack_as_failed(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = _profile({"phase_1": {"services": [{"path": "applications/api", "enabled": True}]}})
    selection = [_entry("applications/api", 1)]
    monkeypatch.setattr(deploy, "_run_stack", lambda *_args, **_kwargs: True)

    monkeypatch.setattr(
        deploy, "resolve_selection_health_containers",
        lambda *_args, **_kwargs: {"ciu-test-api": 30.0},
    )
    monkeypatch.setattr(
        deploy, "run_container_health_gate",
        lambda *_args, **_kwargs: (False, {"healthy": [], "pending": [], "unhealthy": ["ciu-test-api"], "no_healthcheck": [], "not_found": []}),
    )

    assert deploy.action_deploy(
        tmp_path, profile, selection, dry_run=False, ignore_errors=False,
        health_after_phase=True, update_cert_permission=False,
    ) == 1
    summary = capsys.readouterr().out
    assert "applications/api" in summary
    assert "FAILED" in summary


def test_clean_rm_success_proceeds_to_stack_reset(monkeypatch, tmp_path: Path) -> None:
    profile = _profile()
    selection = [_entry("applications/api", 1, health=False)]
    (tmp_path / "applications/api").mkdir(parents=True)
    reset: list[Path] = []
    matching = iter([["ciu-test-api"], []])
    monkeypatch.setattr(deploy, "_matching_containers", lambda *_args, **_kwargs: next(matching))
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {"applications/api": {}})
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda *_args, **_kw: [])
    monkeypatch.setattr(deploy.engine, "reset_service", lambda _config, stack_dir, **_kwargs: reset.append(stack_dir))
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    assert deploy.action_clean(tmp_path, profile, selection, ignore_errors=False) == 0
    assert reset == [(tmp_path / "applications/api").resolve()]


def test_check_reuses_rendered_selection_from_prior_deploy(monkeypatch, tmp_path: Path) -> None:
    profile = _profile()
    selection = [_entry("applications/api", 1, health=False)]
    rendered = {"applications/api": {"api": {}}}
    trace: list[object] = []
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _cfg, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda *_args: selection)
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: trace.append("render") or rendered)
    for name in ("vault_preflight", "provisioning_preflight", "registry_preflight", "governance_slice_preflight", "ensure_workspace_network"):
        monkeypatch.setattr(deploy, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, "action_deploy", lambda *args, **kwargs: trace.append(("deploy", kwargs["rendered"])) or 0)
    monkeypatch.setattr(deploy, "action_check", lambda *args, **kwargs: trace.append(("check", args[3])) or 0)

    assert deploy.main(["--deploy", "--check"]) == 0
    assert trace == ["render", ("deploy", rendered), ("check", rendered)]
