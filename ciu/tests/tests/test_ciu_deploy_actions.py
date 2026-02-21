"""Tests for ciu-deploy action parsing and phase scoping helpers."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy import (  # noqa: E402
    build_action_sequence,
    filter_deployment_phases,
    collect_bake_targets_from_phases,
)


def test_build_action_sequence_aliases_and_order():
    argv = [
        "ciu-deploy",
        "--start",
        "--stop",
        "--clean",
        "--deploy",
    ]

    assert build_action_sequence(argv) == [
        "deploy",
        "stop",
        "clean",
        "deploy",
    ]


def test_filter_deployment_phases():
    phases = [
        {"key": "phase_1", "services": []},
        {"key": "phase_2", "services": []},
        {"key": "phase_4", "services": []},
    ]

    assert filter_deployment_phases(phases, None) == phases
    assert filter_deployment_phases(phases, {"phase_2"}) == [{"key": "phase_2", "services": []}]


def test_collect_bake_targets_from_phases():
    phases = [
        {
            "key": "phase_4",
            "services": [
                {"path": "applications/controller", "enabled": True},
                {"path": "infra/vault", "enabled": True},
                {"path": "tools/admin-debug", "enabled": True},
            ],
        }
    ]

    assert collect_bake_targets_from_phases(phases) == ["admin-debug", "controller"]
