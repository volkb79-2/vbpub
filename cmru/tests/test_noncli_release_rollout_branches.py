"""Behavioral witnesses for non-CLI rollout and release branches."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import release
from cmru.controller.planner import LandscapePlan, PlanStep
from cmru.controller.rollout import RolloutEngine


def _step(required: bool) -> PlanStep:
    return PlanStep(
        plan_id="p", wave_name="w", phase=1, wave_type="canary", nodes=["n"],
        profiles=[], release_tag="demo-v1", manifest_url="u", manifest_sha256="a" * 64,
        config_hash="h", step_id="s", required=required, requires_approval=False,
    )


def test_rollout_publish_completes_nonrequired_wave_without_barrier(monkeypatch):
    calls = []
    backend = SimpleNamespace(_put=lambda path, body: calls.append((path, body)),
                              _get=lambda *_: (404, b"", {}))
    engine = RolloutEngine(backend, "land")
    monkeypatch.setattr(engine, "_write_wave", lambda step: calls.append(("wave", step.wave_name)))
    engine.publish(LandscapePlan("p", "land", [_step(required=False)]))
    assert calls[-1][0].endswith("/status")


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
