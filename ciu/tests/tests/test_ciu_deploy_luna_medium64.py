"""Focused coverage for selected-stack rendering behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def test_render_selected_stacks_deduplicates_ciu_paths_and_skips_shipped(
    monkeypatch, tmp_path
):
    profile = Profile(
        name="focused",
        phase_keys=None,
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
    )
    selection = [
        {
            "path": "infra/core",
            "service": {"path": "infra/core", "name": "core", "enabled": True},
        },
        {
            "path": "infra/core",
            "service": {"path": "infra/core", "name": "core", "enabled": True},
        },
        {
            "path": "applications/shipped",
            "service": {
                "path": "applications/shipped",
                "name": "shipped",
                "enabled": True,
                "shipped": True,
            },
        },
    ]

    monkeypatch.setattr(
        deploy.phases_pkg,
        "service_shipped",
        lambda service: bool(service.get("shipped")),
    )

    def render_stack(stack_dir, *, global_config, preserve_state, ciu_context=None):
        assert global_config is profile.config
        assert preserve_state is True
        return {"stack": stack_dir.name, "rendered": True}

    monkeypatch.setattr(deploy.config_model, "render_stack", render_stack)

    rendered = deploy.render_selected_stacks(tmp_path, profile, selection)

    assert rendered == {"infra/core": {"stack": "core", "rendered": True}}
    assert "applications/shipped" not in rendered
