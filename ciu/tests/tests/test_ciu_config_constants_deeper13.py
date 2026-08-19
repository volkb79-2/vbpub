"""Filename recognition and conversion contracts for CIU configuration files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.config_constants import (  # noqa: E402
    GLOBAL_CONFIG_DEFAULTS,
    GLOBAL_CONFIG_OVERRIDES,
    GLOBAL_CONFIG_WORKTREE_OVERRIDES,
    GLOBAL_CONFIG_RENDERED,
    STACK_CONFIG_DEFAULTS,
    STACK_CONFIG_OVERRIDES,
    STACK_CONFIG_RENDERED,
    get_defaults_template_name,
    get_rendered_config_name,
    is_config_file,
)


@pytest.mark.parametrize(
    ("defaults_name", "rendered_name"),
    [
        (STACK_CONFIG_DEFAULTS, STACK_CONFIG_RENDERED),
        (GLOBAL_CONFIG_DEFAULTS, GLOBAL_CONFIG_RENDERED),
        ("service.defaults.toml.j2", "service.toml"),
    ],
)
def test_exact_defaults_template_suffix_converts_to_rendered_name(defaults_name, rendered_name):
    """Only a complete defaults-template suffix is stripped."""
    assert get_rendered_config_name(defaults_name) == rendered_name


@pytest.mark.parametrize(
    "filename",
    [
        "ciu.defaults.toml.j2.backup",
        "ciu.defaults.toml.j2/child",
        "ciu.toml",
        "notes-ciu.defaults.toml.j2.txt",
    ],
)
def test_non_template_names_are_not_partially_converted(filename):
    """Backups and path fragments must never become a plausible CIU runtime file."""
    assert get_rendered_config_name(filename) == filename


@pytest.mark.parametrize(
    ("rendered_name", "defaults_name"),
    [
        (STACK_CONFIG_RENDERED, STACK_CONFIG_DEFAULTS),
        (GLOBAL_CONFIG_RENDERED, GLOBAL_CONFIG_DEFAULTS),
        ("service.toml", "service.defaults.toml.j2"),
    ],
)
def test_exact_rendered_suffix_converts_to_defaults_template(rendered_name, defaults_name):
    assert get_defaults_template_name(rendered_name) == defaults_name


@pytest.mark.parametrize("filename", ["ciu.toml.backup", "path/ciu.toml", "ciu.toml.j2", "notes.toml.txt"])
def test_non_rendered_names_are_not_partially_converted(filename):
    assert get_defaults_template_name(filename) == filename


@pytest.mark.parametrize(
    "filename",
    [
        GLOBAL_CONFIG_DEFAULTS,
        GLOBAL_CONFIG_OVERRIDES,
        GLOBAL_CONFIG_WORKTREE_OVERRIDES,
        GLOBAL_CONFIG_RENDERED,
        STACK_CONFIG_DEFAULTS,
        STACK_CONFIG_OVERRIDES,
        STACK_CONFIG_RENDERED,
    ],
)
def test_exact_known_config_filenames_are_recognized(filename):
    assert is_config_file(filename) is True


@pytest.mark.parametrize(
    "filename",
    ["ciu.compose.yml", "docker-compose.yml", "ciu.env", "ciu.toml.backup", "subdir/ciu.toml"],
)
def test_non_config_or_path_like_names_are_not_recognized(filename):
    assert is_config_file(filename) is False
