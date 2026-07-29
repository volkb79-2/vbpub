"""One public S7.3 deploy boundary not covered by stack/health failure tests.

The deploy loop performs a live provisioning probe immediately before each
phase.  A failure there is a phase failure: it must stop before starting any
stack from that phase and, absent ``--ignore-errors``, prevent later phases
from running as well.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def test_provisioning_failure_stops_before_phase_dispatch_and_later_phases(monkeypatch, tmp_path: Path) -> None:
    """An unsatisfied requirement cannot start a partial phase (S7.3).

    The negative is material: if the per-phase probe were moved after stack
    dispatch, ``infra/provider`` could start even though the same phase is
    known to be invalid, and ``applications/api`` could then be reached.
    """
    profile = Profile(
        name=None,
        phase_keys=None,
        config={
            "deploy": {
                "project_name": "ciu",
                "environment_tag": "test",
                "phases": {
                    "phase_1": {
                        "services": [
                            {"path": "infra/provider", "enabled": True},
                            {"path": "infra/consumer", "enabled": True},
                        ]
                    },
                    "phase_2": {
                        "services": [{"path": "applications/api", "enabled": True}]
                    },
                },
            }
        },
    )
    selection = deploy.build_selection(profile)
    for entry in selection:
        (tmp_path / entry["path"]).mkdir(parents=True)

    probed: list[list[str]] = []
    dispatched: list[Path] = []

    def fail_first_phase(_root, _profile, entries, _rendered, **_kwargs) -> None:
        paths = [entry["path"] for entry in entries]
        probed.append(paths)
        if paths == ["infra/provider", "infra/consumer"]:
            raise ValueError("unsatisfied pg:application")

    monkeypatch.setattr(deploy, "provisioning_preflight", fail_first_phase)
    monkeypatch.setattr(
        deploy,
        "_run_stack",
        lambda stack_dir, **_kwargs: dispatched.append(stack_dir) or True,
    )

    assert deploy.action_deploy(
        tmp_path,
        profile,
        selection,
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
        rendered={},
    ) == 1

    assert probed == [["infra/provider", "infra/consumer"]]
    assert dispatched == []
