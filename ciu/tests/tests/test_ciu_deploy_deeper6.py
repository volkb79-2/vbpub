"""Remaining S7.7 Compose-authoring boundaries for the deploy health gate.

The health gate must not invent a container target when its rendered Compose
model is unavailable or malformed.  These are deliberately file-only tests:
they exercise the public resolver without Docker, polling, or clock seams.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _profile(*, compose_profiles: list[str] | None = None) -> Profile:
    return Profile(
        name="ops",
        phase_keys=None,
        compose_profiles=compose_profiles or [],
        config={
            "deploy": {
                "project_name": "ciu",
                "environment_tag": "test",
                "phases": {
                    "phase_1": {
                        "services": [
                            {"path": "applications/api", "name": "API", "enabled": True}
                        ]
                    }
                },
            }
        },
    )


def _selection(profile: Profile) -> list[dict]:
    return deploy.build_selection(profile)


def _write_compose(root: Path, contents: str) -> None:
    stack = root / "applications/api"
    stack.mkdir(parents=True)
    (stack / "ciu.compose.yml").write_text(contents, encoding="utf-8")


def test_health_target_resolution_requires_rendered_compose_model(tmp_path):
    """A health action must fail before polling if its render artifact is absent."""
    profile = _profile()

    with pytest.raises(ValueError, match=r"\[S7\.7\].*no rendered ciu\.compose\.yml"):
        deploy.resolve_selection_health_containers(tmp_path, profile, _selection(profile))


def test_health_target_resolution_rejects_invalid_profile_declaration(tmp_path):
    """A scalar Compose profile cannot be treated as an active service profile."""
    profile = _profile()
    _write_compose(
        tmp_path,
        "services:\n  api:\n    container_name: ciu-test-api\n    profiles: ops\n",
    )

    with pytest.raises(ValueError, match=r"\[S7\.7\].*invalid profiles.*list of strings"):
        deploy.resolve_selection_health_containers(tmp_path, profile, _selection(profile))


def test_health_target_resolution_rejects_stack_with_no_active_services(tmp_path):
    """CIU must not pass a health gate merely because every Compose service is dormant."""
    profile = _profile(compose_profiles=["ops"])
    _write_compose(
        tmp_path,
        "services:\n  debug:\n    container_name: ciu-test-debug\n    profiles: [debug]\n",
    )

    with pytest.raises(ValueError, match=r"\[S7\.7\].*no services active.*\['ops'\]"):
        deploy.resolve_selection_health_containers(tmp_path, profile, _selection(profile))


def test_health_target_resolution_rejects_unreadable_yaml_model(tmp_path):
    """Malformed YAML is an authoring error, never an empty successful target set."""
    profile = _profile()
    _write_compose(tmp_path, "services:\n  api: [unterminated\n")

    with pytest.raises(ValueError, match=r"\[S7\.7\].*Cannot read Compose model"):
        deploy.resolve_selection_health_containers(tmp_path, profile, _selection(profile))
