"""Tests for CIU-105 — `[deploy] protected = true` guards `--stop`/`--clean`
against the RIGHTFUL owner's own accidental teardown.

Normative contract: KNOWN_ISSUES_TODO_BACKLOG.md CIU-105's "Behavioral
oracle for the fix": an instance deployed with `protected = true` must
refuse `--stop`/`--clean` under ordinary `-y` alone, naming the additional
step required; the same commands against an instance without the flag must
proceed exactly as they do today (no regression for the common case).

Covers:
- `_validate_deploy_protected` (config_model.py): type validation, run once
  on the final merged config (mirrors CIU-36's landscape_id tests).
- `_refuse_if_protected` (deploy.py): the guard itself, in isolation.
- `deploy.main(["--stop"/"--clean", ...])`: end-to-end wiring — a protected
  instance's teardown never reaches Docker at all without the override
  flag; an unprotected instance's teardown (and a protected one WITH the
  override) is byte-for-byte the pre-CIU-105 behavior.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.config_model import render_global_chain  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


# ---------------------------------------------------------------------------
# _validate_deploy_protected
# ---------------------------------------------------------------------------


def _write_global_defaults(directory: Path, content: str) -> None:
    (directory / "ciu.global.defaults.toml.j2").write_text(content, encoding="utf-8")


def test_protected_absent_defaults_legal(tmp_path):
    """protected is consumer-opt-in: absence must not raise."""
    _write_global_defaults(tmp_path, '[deploy]\nproject_name = "p"\n')
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert "protected" not in result["deploy"]


def test_protected_true_passes(tmp_path):
    _write_global_defaults(tmp_path, '[deploy]\nprotected = true\n')
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert result["deploy"]["protected"] is True


def test_protected_non_bool_fails_naming_key(tmp_path):
    """A string/int value cannot be a boolean flag and must abort, tagged CIU-105."""
    _write_global_defaults(tmp_path, '[deploy]\nprotected = "true"\n')
    with pytest.raises(ValueError) as exc:
        render_global_chain(tmp_path, tmp_path, write_rendered=False)
    message = str(exc.value)
    assert "CIU-105" in message
    assert "protected" in message


def test_protected_validated_on_final_merged_value_not_per_layer(tmp_path):
    """An invalid EARLY-layer value corrected by a later layer must pass —
    same timing contract as CIU-36's landscape_id validation."""
    _write_global_defaults(tmp_path, '[deploy]\nprotected = "yes"\n')
    stack = tmp_path / "infra" / "api"
    stack.mkdir(parents=True)
    _write_global_defaults(stack, "[deploy]\nprotected = true\n")
    result = render_global_chain(stack, tmp_path, write_rendered=False)
    assert result["deploy"]["protected"] is True


def test_protected_worktree_overlay_value_is_validated(tmp_path):
    _write_global_defaults(tmp_path, "[deploy]\nprotected = true\n")
    (tmp_path / "ciu.global.instance.toml.j2").write_text(
        '[deploy]\nprotected = "true"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError) as exc:
        render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert "CIU-105" in str(exc.value)


# ---------------------------------------------------------------------------
# _refuse_if_protected, in isolation
# ---------------------------------------------------------------------------


def _args(**overrides) -> argparse.Namespace:
    ns = argparse.Namespace(i_understand_this_is_protected=False)
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_refuse_returns_none_when_deploy_table_absent():
    assert deploy._refuse_if_protected({}, _args(), "stop") is None


def test_refuse_returns_none_when_protected_false():
    config = {"deploy": {"protected": False}}
    assert deploy._refuse_if_protected(config, _args(), "stop") is None


def test_refuse_returns_one_and_prints_when_protected_and_no_override(capsys):
    config = {"deploy": {"protected": True}}
    assert deploy._refuse_if_protected(config, _args(), "clean") == 1
    out = capsys.readouterr().out
    assert "[CIU-105]" in out
    assert "--clean" in out
    assert "--i-understand-this-is-protected" in out


def test_refuse_returns_none_when_protected_and_override_given():
    config = {"deploy": {"protected": True}}
    args = _args(i_understand_this_is_protected=True)
    assert deploy._refuse_if_protected(config, args, "stop") is None


# ---------------------------------------------------------------------------
# End-to-end via deploy.main(): the dispatch wiring
# ---------------------------------------------------------------------------


def _wire_main(monkeypatch, tmp_path, *, protected: bool):
    profile = Profile(
        name=None,
        phase_keys=None,
        config={
            "deploy": {
                "project_name": "p", "environment_tag": "t", "phases": {},
                **({"protected": True} if protected else {}),
            }
        },
    )
    selection = [{
        "phase_num": 1, "phase_key": "phase_1", "path": "applications/app",
        "name": "app",
        "service": {"path": "applications/app", "name": "app", "enabled": True},
    }]
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: selection)
    return profile


def _forbid_teardown(monkeypatch):
    """Any of these firing means the refusal did NOT short-circuit before
    real teardown mechanics — the exact regression the Behavioral Oracle
    (container still running/present after the attempt) exists to catch."""
    def boom(*_args, **_kwargs):
        pytest.fail("teardown must not run: refusal should short-circuit first")
    monkeypatch.setattr(deploy, "_matching_containers", boom)
    monkeypatch.setattr(deploy, "render_selected_stacks", boom)
    monkeypatch.setattr(deploy, "action_stop", boom)
    monkeypatch.setattr(deploy, "action_clean", boom)


@pytest.mark.parametrize("verb", ["--stop", "--clean"])
def test_protected_instance_refuses_stop_and_clean_under_plain_yes(
    monkeypatch, tmp_path, capsys, verb
):
    _wire_main(monkeypatch, tmp_path, protected=True)
    _forbid_teardown(monkeypatch)

    assert deploy.main([verb, "-y"]) == 1
    assert "[CIU-105]" in capsys.readouterr().out


@pytest.mark.parametrize("verb", ["--stop", "--clean"])
def test_protected_instance_proceeds_with_explicit_override(
    monkeypatch, tmp_path, verb
):
    _wire_main(monkeypatch, tmp_path, protected=True)
    called: list[str] = []
    monkeypatch.setattr(deploy, "action_stop", lambda *_a, **_kw: called.append("stop") or 0)
    monkeypatch.setattr(deploy, "action_clean", lambda *_a, **_kw: called.append("clean") or 0)

    rc = deploy.main([verb, "-y", "--i-understand-this-is-protected"])
    assert rc == 0
    assert called == [verb.lstrip("-")]


@pytest.mark.parametrize("verb", ["--stop", "--clean"])
def test_unprotected_instance_is_completely_unaffected(monkeypatch, tmp_path, verb):
    """No regression for the common (unprotected) case: teardown proceeds
    exactly as before CIU-105, with no override flag needed."""
    _wire_main(monkeypatch, tmp_path, protected=False)
    called: list[str] = []
    monkeypatch.setattr(deploy, "action_stop", lambda *_a, **_kw: called.append("stop") or 0)
    monkeypatch.setattr(deploy, "action_clean", lambda *_a, **_kw: called.append("clean") or 0)

    assert deploy.main([verb, "-y"]) == 0
    assert called == [verb.lstrip("-")]
