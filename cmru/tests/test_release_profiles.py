"""Tests for the release-policy resolution (S-REL): artifacts → (git_tag,
commit_generated), the OCI-vs-tag guard, multi-output union, and overrides.

Stdlib only — no network, no git.
"""
from __future__ import annotations

import pytest

from cmru import cli


def _vspec(strategy: str = "scm"):
    return cli.VersionSpec(strategy=strategy)


def test_explicit_wheel_release_mints_tag():
    artifacts, git_tag, gen = cli._parse_release_policy(
        {"artifacts": ["wheel"], "release": {"git_tag": True}}, "ciu", _vspec("scm")
    )
    assert artifacts == ("wheel",)
    assert git_tag is True
    assert gen == ()


def test_retired_singular_artifact_is_rejected():
    with pytest.raises(ValueError, match="artifacts must be a list"):
        cli._parse_release_policy({"artifact": "wheel"}, "ciu", _vspec("scm"))


def test_oci_image_and_none_strategy_no_tag():
    artifacts, git_tag, gen = cli._parse_release_policy(
        {"artifacts": ["oci-image"], "release": {"git_tag": False, "commit_generated": ["package-manifests-versioned"]}},
        "mdt",
        _vspec("none"),
    )
    assert artifacts == ("oci-image",)          # alias normalized
    assert git_tag is False                      # registry publish, no git tag
    assert gen == ("package-manifests-versioned",)


def test_oci_with_scm_can_tag_only_when_the_project_explicitly_says_so():
    _, git_tag, _ = cli._parse_release_policy(
        {"artifacts": ["oci-image"], "release": {"git_tag": True}}, "image", _vspec("scm")
    )
    assert git_tag is True


def test_multi_output_uses_its_explicit_tag_policy():
    _, git_tag, _ = cli._parse_release_policy(
        {"artifacts": ["oci-image", "bundle"], "release": {"git_tag": True}}, "pwmcp", _vspec("scm")
    )
    assert git_tag is True


def test_release_git_tag_override():
    _, git_tag, _ = cli._parse_release_policy(
        {"artifacts": ["wheel"], "release": {"git_tag": False}}, "x", _vspec("scm")
    )
    assert git_tag is False


def test_release_git_tag_is_required():
    with pytest.raises(ValueError, match="must be explicitly true or false"):
        cli._parse_release_policy({"artifacts": ["wheel"]}, "x", _vspec("scm"))


def test_unknown_artifact_rejected():
    with pytest.raises(ValueError, match="unknown artifact type"):
        cli._parse_release_policy({"artifacts": ["sdist"]}, "x", _vspec("scm"))
