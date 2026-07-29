"""Public profile composition coverage for independent override keys."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg.profiles import resolve_profiles


def test_profile_resolution_combines_independent_environment_overrides() -> None:
    """Selecting compatible profiles preserves each profile's operator setting."""
    config = {
        "deploy": {
            "profiles": {
                "base": {"env_overrides": {"LOG_LEVEL": "INFO"}},
                "region": {"env_overrides": {"REGION": "test-west"}},
            }
        }
    }

    resolved = resolve_profiles(config, ["base", "region"], env={})

    assert resolved.name == "base,region"
    assert resolved.env_overrides == {
        "LOG_LEVEL": "INFO",
        "REGION": "test-west",
    }
