"""Behavioral witnesses for non-CLI release publication branches."""
from __future__ import annotations

import pytest

from cmru import release


def test_release_publish_updates_existing_release_and_dev_pointer_uploads(tmp_path):
    api = release.GitHubReleases("o", "r", "t", "org")
    calls = []
    api.get_release_by_tag = lambda tag: {"id": 7, "upload_url": "https://upload/{id}"}
    api.update_release = lambda *args: calls.append(("update", args))
    api.list_assets = lambda _rid: []
    api.upload_asset = lambda *args: calls.append(("upload", args))
    asset = tmp_path / "demo.whl"
    asset.write_bytes(b"wheel")
    assert api.publish("demo-v1", "title", "notes", [asset])["id"] == 7
    assert calls[0][0] == "update"
    invalid = release.GitHubReleases("o", "r", "t", "org")
    invalid.get_release_by_tag = lambda tag: {"upload_url": "https://upload/{id}"}
    with pytest.raises(SystemExit):
        invalid.publish("demo-v1", "title", "notes", [])

    api.publish = lambda *args, **kwargs: calls.append(("pointer", args))
    result = release.publish_versioned(api, prefix="demo", version="1.0.0.dev1",
                                       asset_path=asset, notes="dev", latest_pointer=True)
    assert result["release_tag"] is None
    assert any(item[0] == "pointer" for item in calls)
    release.publish_versioned(api, prefix="demo", version="1.0.0.dev2",
                              asset_path=asset, notes="dev", latest_pointer=False)
    variant = release.VariantArtifact("py311", asset)
    variants = release.publish_versioned_variants(
        api, prefix="demo", version="1.0.0", variants=[variant],
        asset_suffix=".whl", notes="variant", latest_pointer=False,
    )
    assert variants["release_tag"] == "demo-v1.0.0"
