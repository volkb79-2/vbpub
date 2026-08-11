"""`wheel-publish --extra-asset`: attach companion files to a wheel release.

Added for assay, which publishes a reproducible zipapp and a hash-bound release
manifest beside its wheel. `publish_versioned` has always accepted
`extra_assets`; `cmd_wheel_publish` simply never exposed it, so a `wheel`
project could not attach a companion artifact without reimplementing the
release call.

The flag is purely additive: it defaults to empty, and the differential test
below asserts that a publish with no `--extra-asset` produces exactly the two
assets it always did. That is the property the other six projects in
`cmru.toml` depend on.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from cmru import handlers, release


class _FakeGH:
    """Records publish() calls; no network. Mirrors test_variants.py's stub."""

    def __init__(self) -> None:
        self.published: list = []

    def asset_download_url(self, tag: str, asset_name: str) -> str:
        return f"https://example.test/{tag}/{asset_name}"

    def publish(self, tag, title, notes, assets, *, recreate=False, target_commitish=None):
        self.published.append({
            "tag": tag,
            "asset_names": [Path(a).name for a in assets],
        })
        return {"id": len(self.published)}


def _wheel(dist: Path, name: str = "assay", version: str = "1.2.3") -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    wheel = dist / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n",
        )
    return wheel


@pytest.fixture()
def published(monkeypatch, tmp_path):
    """Drive `cmd_wheel_publish` against the stub, returning the recorded call."""
    recorded: dict = {}
    gh = _FakeGH()

    monkeypatch.setattr(handlers, "GitHubReleases", lambda *a, **k: gh)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "token")
    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_REPO", "repo")

    def run(extra: list[str]) -> dict:
        project = tmp_path / "assay"
        _wheel(project / "dist")
        argv = ["wheel-publish", "--prefix", "assay", "--cwd", str(project)]
        for item in extra:
            argv += ["--extra-asset", item]
        handlers.main(argv)
        recorded["gh"] = gh
        return gh.published[0]

    return run


def test_no_extra_asset_publishes_exactly_the_wheel_and_its_sidecar(published):
    """The differential control. Six other projects publish through this handler
    and none passes the new flag; their asset list must not move."""
    call = published([])
    assert call["asset_names"] == [
        "assay-1.2.3-py3-none-any.whl",
        "assay-1.2.3-py3-none-any.whl.sha256",
    ]


def test_extra_assets_are_attached_to_the_same_release(published, tmp_path):
    companion = tmp_path / "assay-1.2.3.pyz"
    companion.write_bytes(b"zipapp")
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    call = published([str(companion), str(manifest)])

    assert call["asset_names"] == [
        "assay-1.2.3-py3-none-any.whl",
        "assay-1.2.3-py3-none-any.whl.sha256",
        "assay-1.2.3.pyz",
        "release-manifest.json",
    ]


def test_an_extra_asset_that_matches_nothing_is_refused_before_publishing(published, tmp_path):
    """Fail closed rather than publishing a partial release: a typo'd companion
    path must not produce a release whose notes advertise an artifact that was
    never uploaded."""
    with pytest.raises(SystemExit, match="matched no existing file"):
        published([str(tmp_path / "absent.pyz")])


def test_an_extra_asset_may_be_a_GLOB_because_the_filename_carries_the_version(
    published, tmp_path
):
    """The step in cmru.toml cannot name `assay-1.2.3.pyz` literally -- it does
    not know the version being cut. A glob is the only workable spelling."""
    companion = tmp_path / "assay-1.2.3.pyz"
    companion.write_bytes(b"zipapp")
    call = published([str(tmp_path / "assay-*.pyz")])
    assert "assay-1.2.3.pyz" in call["asset_names"]


def test_publish_versioned_itself_still_accepts_extra_assets_directly(tmp_path):
    """The library half, so the handler is provably plumbing rather than the
    feature: this path predates the flag."""
    dist = tmp_path / "dist"
    wheel = _wheel(dist)
    companion = dist / "assay-1.2.3.pyz"
    companion.write_bytes(b"zipapp")
    gh = _FakeGH()

    result = release.publish_versioned(
        gh, prefix="assay", version="1.2.3", asset_path=wheel,
        notes="assay 1.2.3", extra_assets=[companion],
    )

    assert result["release_tag"] == "assay-v1.2.3"
    assert "assay-1.2.3.pyz" in gh.published[0]["asset_names"]
