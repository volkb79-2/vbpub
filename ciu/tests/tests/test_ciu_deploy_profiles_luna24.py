"""Public profile-table validation and composition contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu.deploy_pkg.profiles import resolve_profiles  # noqa: E402


def _config(profile: dict, *, base_topology: dict | None = None) -> dict:
    config = {"deploy": {"profiles": {"selected": profile}}}
    if base_topology is not None:
        config["topology"] = base_topology
    return config


def test_profile_resolution_preserves_topology_and_adds_new_override_key():
    """A valid profile composes nested overrides without mutating its input."""
    original = {
        "services": {"api": {"host": "api.internal", "port": 8080}},
    }
    config = _config(
        {"topology_overrides": {"services": {"api": {"tls": True}}}},
        base_topology=original,
    )

    resolved = resolve_profiles(config, ["selected"], env={})

    assert resolved.config["topology"] == {
        "services": {
            "api": {"host": "api.internal", "port": 8080, "tls": True},
        },
    }
    assert config["topology"] == original


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("phases", "phase_1", "phases.*must be a list"),
        ("env_overrides", ["LOG_LEVEL=DEBUG"], "env_overrides.*must be a dict"),
        ("topology_overrides", ["invalid"], "topology_overrides.*must be a dict"),
    ],
)
def test_profile_resolution_rejects_malformed_profile_fields(field, value, message):
    """Malformed public profile fields fail with actionable ValueErrors."""
    with pytest.raises(ValueError, match=message):
        resolve_profiles(_config({field: value}), ["selected"], env={})
