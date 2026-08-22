"""Remaining public deploy action contracts at the Docker/render boundary.

The tests keep Docker and health-image probing deterministic, but assert the
observable action outcome: which rendered compose files are eligible, whether a
strict preflight can block, and whether clean/build preserve their advertised
failure and cleanup semantics.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _profile() -> Profile:
    return Profile(
        name="test",
        phase_keys=None,
        config={
            "deploy": {
                "project_name": "ciu",
                "environment_tag": "test",
                "phases": {},
            }
        },
    )


def test_preflight_prefers_rendered_compose_and_warns_without_any_compose(
    monkeypatch, tmp_path, capsys
):
    """Preflight examines one authoritative compose input per selected stack.

    An unrendered stack is not silently treated as a healthcheck-free success;
    users receive the render-first warning.  If both files exist, the rendered
    ``ciu.compose.yml`` is the authoritative input rather than the shipped base.
    """
    stack = tmp_path / "applications/api"
    stack.mkdir(parents=True)
    rendered = stack / "ciu.compose.yml"
    rendered.write_text("services: {}\n", encoding="utf-8")
    (stack / "docker-compose.yml").write_text("services: {stale: {}}\n", encoding="utf-8")
    checked: list[Path] = []

    from ciu.deploy_pkg import health

    def fake_probe(paths, *, warn_fn, info_fn):
        checked.extend(paths)
        return []

    monkeypatch.setattr(health, "preflight_probe", fake_probe)
    rc = deploy.action_healthcheck_preflight(
        tmp_path,
        _profile(),
        [{"path": "applications/api"}, {"path": "tools/not-rendered"}],
    )

    assert rc == 0
    assert checked == [rendered]
    out = capsys.readouterr().out
    assert "tools/not-rendered" in out
    assert "run 'ciu render' first" in out


def test_preflight_warnings_are_advisory_unless_strict(monkeypatch, tmp_path):
    """``--strict`` turns actual image-tool findings into a blocking verdict."""
    stack = tmp_path / "infra/db"
    stack.mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    from ciu.deploy_pkg import health

    monkeypatch.setattr(health, "preflight_probe", lambda *_args, **_kw: ["postgres lacks pg_isready"])
    selection = [{"path": "infra/db"}]

    assert deploy.action_healthcheck_preflight(tmp_path, _profile(), selection, strict=False) == 0
    assert deploy.action_healthcheck_preflight(tmp_path, _profile(), selection, strict=True) == 1


def test_preflight_without_rendered_compose_does_not_probe_docker(monkeypatch, tmp_path, capsys):
    """The no-rendered-stack path is an actionable warning, not a Docker call."""
    from ciu.deploy_pkg import health

    monkeypatch.setattr(
        health,
        "preflight_probe",
        lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("must not probe without compose")),
    )

    assert deploy.action_healthcheck_preflight(tmp_path, _profile(), [{"path": "missing"}]) == 0
    assert "No rendered compose files found" in capsys.readouterr().out


def test_clean_restores_callers_compose_profiles_after_stack_reset_failure(monkeypatch, tmp_path):
    """Best-effort clean must not leak its all-profiles override into later work."""
    stack = tmp_path / "applications/api"
    stack.mkdir(parents=True)
    profile = _profile()
    seen_profiles: list[str | None] = []

    monkeypatch.setattr(deploy, "_matching_containers", lambda *_args, **_kw: [])
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {"applications/api": {}})
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda *_args, **_kw: [])

    def fake_reset(*_args, **_kwargs):
        seen_profiles.append(os.environ.get("COMPOSE_PROFILES"))
        raise RuntimeError("compose down failed")

    monkeypatch.setattr(deploy.engine, "reset_service", fake_reset)
    monkeypatch.setenv("COMPOSE_PROFILES", "caller-profile")

    assert deploy.action_clean(
        tmp_path, profile, [{"path": "applications/api"}], ignore_errors=True
    ) == 1
    assert seen_profiles == ["*"]
    assert os.environ["COMPOSE_PROFILES"] == "caller-profile"


def test_clean_stops_at_first_reset_failure_without_ignore_errors(monkeypatch, tmp_path):
    """Without break-glass continuation, a failed reset suppresses later stacks."""
    for rel in ("applications/first", "applications/later"):
        (tmp_path / rel).mkdir(parents=True)
    profile = _profile()
    reset_paths: list[Path] = []

    monkeypatch.setattr(deploy, "_matching_containers", lambda *_args, **_kw: [])
    monkeypatch.setattr(
        deploy,
        "render_selected_stacks",
        lambda *_args, **_kw: {"applications/first": {}, "applications/later": {}},
    )
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda *_args, **_kw: [])

    def fake_reset(_config, stack_dir, **_kwargs):
        reset_paths.append(stack_dir)
        raise RuntimeError("first reset failed")

    monkeypatch.setattr(deploy.engine, "reset_service", fake_reset)

    assert deploy.action_clean(
        tmp_path,
        profile,
        [{"path": "applications/first"}, {"path": "applications/later"}],
        ignore_errors=False,
    ) == 1
    assert reset_paths == [tmp_path / "applications/first"]


def test_build_uses_selected_application_targets_and_propagates_docker_failure(monkeypatch, tmp_path):
    """Build targets application/tool stacks only and a failed bake remains red."""
    commands: list[list[str]] = []

    def failed_docker(cmd, **_kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "build failed")

    monkeypatch.setattr(deploy.procutil, "docker", failed_docker)
    monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
    assert deploy.action_build(
        tmp_path,
        [
            {"path": "applications/api"},
            {"path": "tools/admin"},
            {"path": "infrastructure/network"},
        ],
        use_cache=False,
    ) == 1
    assert commands == [["buildx", "bake", "admin", "api", "--load",
                         "--set", "*.labels.org.opencontainers.image.revision=abc12345", "--no-cache"]]


def test_list_actions_report_enabled_services_and_profile_details(capsys):
    """Inspection actions expose the effective deploy surface, not disabled entries."""
    config = {
        "deploy": {
            "control": {"api": False},
            "phases": {
                "phase_10": {"name": "Later", "services": [{"path": "tools/admin", "name": "Admin"}]},
                "phase_2": {
                    "name": "Core",
                    "services": [
                        {"path": "applications/api", "name": "API"},
                        {"path": "applications/disabled", "name": "Disabled", "enabled": False},
                    ],
                },
            },
            "profiles": {
                "ops": {
                    "phases": [2],
                    "stacks": ["tools/admin"],
                    "compose_profiles": ["metrics"],
                    "topology_overrides": {"replicas": 2},
                }
            },
        }
    }

    assert deploy.action_list_phases(config) == 0
    assert deploy.action_list_profiles(config) == 0
    out = capsys.readouterr().out
    assert out.index("phase_2 (Core)") < out.index("phase_10 (Later)")
    assert "- API [applications/api]" in out
    assert "Disabled" not in out
    assert "ops:" in out
    assert "compose_profiles: ['metrics']" in out
