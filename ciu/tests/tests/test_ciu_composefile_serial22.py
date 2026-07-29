"""Overlay rendering fallback for a non-mapping compose document."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.composefile import ConfigFileMount, generate_overlay


def test_overlay_preserves_configfile_mount_when_rendered_compose_is_a_list(
    tmp_path: Path,
) -> None:
    """A malformed rendered document cannot cause a configfile mount to vanish."""
    stack = tmp_path / "stack"
    rendered = stack / ".ciu" / "rendered" / "api" / "etc" / "app" / "app.conf"
    rendered.parent.mkdir(parents=True)
    rendered.write_text("safe configuration", encoding="utf-8")
    mount = ConfigFileMount("api", "main", rendered, "/etc/app/app.conf")

    overlay = generate_overlay(
        stack,
        {},
        [mount],
        compose_yaml_text="- not-a-compose-document\n",
        repo_root=tmp_path,
        physical_root=tmp_path,
    )

    assert overlay == stack / ".ciu" / "ciu.compose.overlay.yml"
    document = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    assert set(document["services"]) == {"api"}
    assert document["services"]["api"]["volumes"] == [{
        "type": "bind",
        "source": str(rendered.parent),
        "target": "/etc/app",
        "read_only": True,
    }]
