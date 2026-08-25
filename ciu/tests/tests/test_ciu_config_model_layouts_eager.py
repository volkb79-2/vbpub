"""Eager S11 validation for declared layouts/exec-targets/vendor_images
(QOL-11, CIU-QOL-11).

`config_model.validate_declared_features` composes three ALREADY-CORRECT
validators — `deploy_pkg.layouts.resolve_layout` (S7.5c),
`worktree.resolve_exec_targets_config` (S16.7), and a new vendor_images shape
check (S17.5) — into a single call, then that call is wired into every
config-render path: single-stack `engine.main_execution` (Step 5) and
profile-mode `deploy.action_check`. Before this package, a malformed
globally-declared layout/exec-target/vendor_images entry was only caught when
the specific feature's own command happened to run this invocation (`ciu up
--layout`, `ciu worktree exec`, `ciu provenance`) — a plain `ciu up --dir`
or `ciu check` run left it undetected.

This file covers `validate_declared_features` directly (O1), plus one
integration test each proving it is actually reached from `main_execution`
(O2) and `action_check` (O3) without the specific feature being invoked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import config_model, deploy, engine  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402
from ciu.worktree import WorktreeError  # noqa: E402


# ---------------------------------------------------------------------------
# O1 — validate_declared_features, direct unit tests
# ---------------------------------------------------------------------------


def test_all_three_tables_absent_is_a_full_noop():
    """No [deploy].layouts, no [ciu.worktree].exec_targets, no
    [deploy.provenance].vendor_images — zero iterations, nothing raised."""
    config_model.validate_declared_features({}, {})


def test_one_valid_layout_passes():
    cfg = {
        "deploy": {
            "profiles": {"core": {"phases": ["phase_1"]}},
            "layouts": {
                "dev-local": {
                    "environment": "dev",
                    "hosts": {"devbox": {"bundles": ["core"]}},
                },
            },
        }
    }
    config_model.validate_declared_features(cfg, {"devbox": {}})


def test_multiple_valid_layouts_pass():
    cfg = {
        "deploy": {
            "profiles": {
                "core": {"phases": ["phase_1"]},
                "db": {"phases": ["phase_1"]},
            },
            "layouts": {
                "dev-local": {
                    "environment": "dev",
                    "hosts": {"devbox": {"bundles": ["core"]}},
                },
                "three-host": {
                    "environment": "prod",
                    "hosts": {
                        "edge-a": {"bundles": ["core"]},
                        "backend": {"bundles": ["db"]},
                    },
                },
            },
        }
    }
    hosts = {"devbox": {}, "edge-a": {}, "backend": {}}
    config_model.validate_declared_features(cfg, hosts)


def test_layout_referencing_unknown_host_raises():
    """Mirrors test_ciu_deploy_layouts.py's own unknown-host fixture shape
    (that file is read-only for this package — the shape is reused here,
    not the file)."""
    cfg = {
        "deploy": {
            "profiles": {"core": {"phases": ["phase_1"]}},
            "layouts": {
                "x": {
                    "environment": "dev",
                    "hosts": {
                        "devbox": {"bundles": ["core"]},
                        "ghost": {"bundles": ["core"]},
                    },
                }
            },
        }
    }
    with pytest.raises(
        ValueError,
        match=r"\[S7\.5c\] Layout 'x': host 'ghost' is not in the hosts inventory",
    ):
        config_model.validate_declared_features(cfg, {"devbox": {}})


def test_exec_targets_table_absent_passes():
    config_model.validate_declared_features({"ciu": {}}, {})


def test_exec_targets_valid_passes():
    cfg = {
        "ciu": {
            "worktree": {
                "exec_targets": {
                    "tester": {"stack": "test", "service": "tester", "workdir": "/workspace"},
                }
            }
        }
    }
    config_model.validate_declared_features(cfg, {})


def test_malformed_exec_targets_raises():
    """Mirrors test_ciu_worktree.py's own 'entry not a table' exec-target
    fixture (that file is read-only for this package)."""
    cfg = {"ciu": {"worktree": {"exec_targets": {"tester": "nope"}}}}
    with pytest.raises(WorktreeError, match=r"\[S16\.7\] exec target 'tester' must be a table"):
        config_model.validate_declared_features(cfg, {})


def test_vendor_images_absent_key_passes():
    config_model.validate_declared_features({"deploy": {"provenance": {}}}, {})


def test_vendor_images_no_provenance_table_passes():
    config_model.validate_declared_features({"deploy": {}}, {})


def test_vendor_images_bare_string_raises_tagged_S17_5():
    """A classic Python footgun: `for v in "nginx"` would silently 'validate'
    four single-character non-empty strings. The bare-string case must be
    rejected outright, never iterated."""
    cfg = {"deploy": {"provenance": {"vendor_images": "hashicorp/vault:1.15"}}}
    with pytest.raises(
        ValueError,
        match=r"\[S17\.5\] \[deploy\.provenance\] vendor_images must be a list.*got str",
    ):
        config_model.validate_declared_features(cfg, {})


def test_vendor_images_non_string_element_raises_tagged_S17_5_with_index():
    cfg = {"deploy": {"provenance": {"vendor_images": ["hashicorp/vault:1.15", 5]}}}
    with pytest.raises(
        ValueError,
        match=r"\[S17\.5\] \[deploy\.provenance\] vendor_images\[1\] must be a non-empty string.*got 5",
    ):
        config_model.validate_declared_features(cfg, {})


def test_vendor_images_empty_string_element_raises_tagged_S17_5_with_index():
    cfg = {"deploy": {"provenance": {"vendor_images": [""]}}}
    with pytest.raises(
        ValueError,
        match=r"\[S17\.5\] \[deploy\.provenance\] vendor_images\[0\] must be a non-empty string",
    ):
        config_model.validate_declared_features(cfg, {})


def test_vendor_images_valid_list_of_strings_passes():
    cfg = {"deploy": {"provenance": {"vendor_images": ["hashicorp/vault:1.15", "redis:7"]}}}
    config_model.validate_declared_features(cfg, {})


# ---------------------------------------------------------------------------
# O2 — wired into engine.main_execution's single-stack Step 5
# ---------------------------------------------------------------------------

_BAD_GLOBAL_LAYOUT = {
    "ciu": {"auto_connect_network": False},
    "deploy": {
        "profiles": {"core": {"phases": ["phase_1"]}},
        "layouts": {
            "prod": {
                "environment": "prod",
                "hosts": {"ghost-host": {"bundles": ["core"]}},
            }
        },
    },
}

_GOOD_STACK = {"demo": {}}


def test_main_execution_surfaces_bad_globally_declared_layout_without_layout_flag(
    tmp_path, monkeypatch
):
    """O2: a `ciu up --dir` run that never passes --layout still catches a
    malformed globally-declared layout, because layouts are validated
    against the workspace's GLOBAL config (config_model.render_global_chain's
    result) on every single-stack render — not only when `--layout` selects
    one. Config-rendering/network/dependency machinery is stubbed here (this
    test is about main_execution's Step-5 wiring, not the render chain or
    Docker); resolve_layout itself is the REAL function.
    """
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
    monkeypatch.setattr(config_model, "render_global_chain", lambda *a, **k: _BAD_GLOBAL_LAYOUT)
    monkeypatch.setattr(config_model, "render_stack", lambda *a, **k: dict(_GOOD_STACK))
    monkeypatch.setattr(engine.hosts, "load_hosts", lambda repo_root: {})

    with pytest.raises(
        ValueError,
        match=r"\[S7\.5c\] Layout 'prod': host 'ghost-host' is not in the hosts inventory",
    ):
        engine.main_execution(working_dir=tmp_path, define_root=tmp_path)


_BAD_GLOBAL_EXEC_TARGETS = {
    "ciu": {"auto_connect_network": False, "worktree": {"exec_targets": {"tester": "nope"}}},
}


def test_main_execution_translates_exec_targets_worktree_error_to_valueerror_exit_2(
    tmp_path, monkeypatch
):
    """Adversarial-review fix: a malformed globally-declared exec_targets
    table raises `worktree.WorktreeError` (not `ValueError`) from
    `resolve_exec_targets_config` — Step 5's OTHER two checks
    (`validate_stack_shape`/`validate_stack_provisioning`) both raise
    `ValueError`, and `deploy.action_check` already translates this same
    input to exit 2. `main_execution` must match: `WorktreeError` from
    `validate_declared_features` is caught and re-raised as `ValueError` so
    S10.3's `_exit_code_for` maps it to exit 2, not the generic exit 1 a
    bare `WorktreeError` would otherwise fall through to.
    """
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
    monkeypatch.setattr(
        config_model, "render_global_chain", lambda *a, **k: _BAD_GLOBAL_EXEC_TARGETS
    )
    monkeypatch.setattr(config_model, "render_stack", lambda *a, **k: dict(_GOOD_STACK))
    monkeypatch.setattr(engine.hosts, "load_hosts", lambda repo_root: {})

    with pytest.raises(ValueError, match=r"\[S16\.7\]"):
        engine.main_execution(working_dir=tmp_path, define_root=tmp_path)


# ---------------------------------------------------------------------------
# O3 — wired into deploy.action_check (runs even with an empty selection)
# ---------------------------------------------------------------------------


def test_action_check_surfaces_bad_globally_declared_layout_with_empty_selection(
    tmp_path, monkeypatch, capsys
):
    """O3: `ciu check` catches the same class of globally-declared defect —
    and runs even when `selection` is empty, since a malformed
    globally-declared layout is a real defect regardless of what's selected
    this run."""
    monkeypatch.setattr(deploy.hosts_pkg, "load_hosts", lambda repo_root: {})
    profile = Profile(config=_BAD_GLOBAL_LAYOUT)

    rc = deploy.action_check(tmp_path, profile, [], {})

    assert rc == 2
    out = capsys.readouterr().out
    assert "[S7.5c] Layout 'prod': host 'ghost-host' is not in the hosts inventory" in out
