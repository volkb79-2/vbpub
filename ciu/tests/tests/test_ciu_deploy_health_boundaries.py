"""S7.7 health orchestration boundary contracts.

These tests use only synthetic Compose models and the deploy Docker seam.  In
particular they prove that CIU never derives a runtime target from a friendly
phase label, and that malformed inspect/Compose data fails closed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg import health  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _profile(path: str = "infra/cache") -> Profile:
    return Profile(
        name=None,
        phase_keys=None,
        config={
            "deploy": {
                "project_name": "project",
                "environment_tag": "prod",
                "phases": {
                    "phase_1": {
                        "services": [{"path": path, "name": "Friendly cache", "enabled": True}]
                    }
                },
            }
        },
    )


def _write_compose(root: Path, path: str, contents: str) -> None:
    stack = root / path
    stack.mkdir(parents=True)
    (stack / "ciu.compose.yml").write_text(contents, encoding="utf-8")


def test_health_gate_inspects_exact_resolved_container_names(monkeypatch):
    inspected: list[str] = []

    def inspect(name: str):
        inspected.append(name)
        return {"Health": {"Status": "healthy"}}

    monkeypatch.setattr(deploy, "_inspect_state", inspect)
    passed, summary = deploy.run_container_health_gate(
        ["project-prod-cache", "project-prod-worker"], timeout_s=0, interval_s=0
    )

    assert passed is True
    assert inspected == ["project-prod-cache", "project-prod-worker"]
    assert summary["healthy"] == inspected


def test_health_gate_pending_then_healthy_uses_deterministic_polling(monkeypatch):
    states = iter([
        {"Health": {"Status": "starting"}},
        {"Health": {"Status": "healthy"}},
    ])
    monkeypatch.setattr(deploy, "_inspect_state", lambda _name: next(states))
    ticks = iter([0.0, 0.0, 1.0])
    slept: list[float] = []
    real_wait_for_gate = health.wait_for_gate
    monkeypatch.setattr(
        deploy.health_pkg,
        "wait_for_gate",
        lambda check_fn, **_kw: real_wait_for_gate(
            check_fn,
            timeout_s=5,
            interval_s=1,
            clock=lambda: next(ticks),
            sleep_fn=slept.append,
        ),
    )

    passed, summary = deploy.run_container_health_gate(
        ["project-prod-cache"], timeout_s=5, interval_s=1
    )

    assert passed is True
    assert summary["healthy"] == ["project-prod-cache"]
    assert slept == [1]


def test_health_gate_timeout_preserves_pending_summary(monkeypatch):
    monkeypatch.setattr(
        deploy, "_inspect_state", lambda _name: {"Health": {"Status": "starting"}}
    )
    passed, summary = deploy.run_container_health_gate(
        ["project-prod-cache"], timeout_s=0, interval_s=0
    )

    assert passed is False
    assert summary["pending"] == ["project-prod-cache"]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ([], "not-found"),
        ({"Health": "starting"}, "unhealthy"),
        ({"Health": []}, "unhealthy"),
        ({"Health": {"Status": []}}, "unhealthy"),
    ],
)
def test_malformed_docker_health_data_fails_closed(state, expected):
    assert health.classify(state) == expected


@pytest.mark.parametrize(
    "contents",
    ["- not-a-compose-mapping\n", "services: []\n", "services: {cache: not-a-mapping}\n"],
)
def test_malformed_compose_health_model_is_authoring_error(tmp_path, contents):
    profile = _profile()
    _write_compose(tmp_path, "infra/cache", contents)

    with pytest.raises(ValueError, match=r"\[S7\.7\].*(must be a mapping|has no services)"):
        deploy.resolve_selection_health_containers(
            tmp_path, profile, deploy.build_selection(profile)
        )


def test_no_healthcheck_is_explicit_warning_not_health_failure(monkeypatch, tmp_path, capsys):
    profile = _profile()
    _write_compose(
        tmp_path,
        "infra/cache",
        "services:\n  cache:\n    container_name: project-prod-cache\n",
    )
    monkeypatch.setattr(
        deploy,
        "run_container_health_gate",
        lambda names, **_kw: (True, {
            "healthy": [], "pending": [], "unhealthy": [],
            "no_healthcheck": list(names), "not_found": [],
        }),
    )

    assert deploy.action_healthcheck(tmp_path, profile, deploy.build_selection(profile)) == 0
    output = capsys.readouterr().out
    assert "no_healthcheck: project-prod-cache" in output
    assert "health gate passed" in output
