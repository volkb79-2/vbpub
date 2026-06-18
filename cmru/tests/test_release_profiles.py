"""Tests for the publish-profile resolution (S-REL): artifacts → (mint_tag,
commit_generated), the OCI-vs-tag guard, multi-output union, and overrides.

Stdlib only — no network, no git.
"""
from __future__ import annotations

import pytest

from cmru import cli


def _vspec(strategy: str = "scm"):
    return cli.VersionSpec(strategy=strategy)


def test_wheel_profile_mints_tag():
    artifacts, mint_tag, gen = cli._resolve_release_profile(
        {"artifacts": ["wheel"]}, "ciu", _vspec("scm")
    )
    assert artifacts == ("wheel",)
    assert mint_tag is True
    assert gen == ()


def test_legacy_singular_artifact_maps_to_profile():
    artifacts, mint_tag, _ = cli._resolve_release_profile(
        {"artifact": "wheel"}, "ciu", _vspec("scm")
    )
    assert artifacts == ("wheel",)
    assert mint_tag is True


def test_oci_alias_and_none_strategy_no_tag():
    artifacts, mint_tag, gen = cli._resolve_release_profile(
        {"artifacts": ["oci"], "release": {"commit_generated": ["package-manifests-versioned"]}},
        "mdt",
        _vspec("none"),
    )
    assert artifacts == ("oci-image",)          # alias normalized
    assert mint_tag is False                     # registry publish, no git tag
    assert gen == ("package-manifests-versioned",)


def test_oci_with_scm_is_rejected():
    """The exact bug that produced modern-debian-tools-python-debug-v0.1.0."""
    with pytest.raises(ValueError, match="oci-image artifact must use version.strategy='none'"):
        cli._resolve_release_profile({"artifacts": ["oci-image"]}, "mdt", _vspec("scm"))


def test_multi_output_unions_capabilities():
    # oci-image + bundle: bundle wants a tag, so the union mints one (unless delegated).
    _, mint_tag, _ = cli._resolve_release_profile(
        {"artifacts": ["oci-image", "bundle"]}, "pwmcp", _vspec("scm")
    )
    assert mint_tag is True


def test_delegated_forces_no_tag_even_with_taggy_artifact():
    _, mint_tag, _ = cli._resolve_release_profile(
        {"artifacts": ["oci-image", "bundle"]}, "pwmcp", _vspec("delegated")
    )
    assert mint_tag is False                      # delegated → project owns the tag


def test_release_git_tag_override():
    _, mint_tag, _ = cli._resolve_release_profile(
        {"artifacts": ["wheel"], "release": {"git_tag": False}}, "x", _vspec("scm")
    )
    assert mint_tag is False


def test_unknown_artifact_rejected():
    with pytest.raises(ValueError, match="unknown artifact/profile"):
        cli._resolve_release_profile({"artifacts": ["sdist"]}, "x", _vspec("scm"))
