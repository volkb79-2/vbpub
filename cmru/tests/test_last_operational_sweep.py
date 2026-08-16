"""Concrete final sweep for remaining operational dispatch branches."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import bundle, release
from cmru.agent import cli as agent_cli


def test_agent_cli_main_dispatches_status_and_propagates_result(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_cli, "cmd_status", lambda args: calls.append(args.scope) or 3)
    with pytest.raises(SystemExit) as error:
        agent_cli.main(["status"])
    assert error.value.code == 3 and calls == ["user"]


def test_bundle_write_tar_uses_source_file_content_and_executable_mode(tmp_path):
    source = tmp_path / "run.sh"; source.write_text("#!/bin/sh\n", encoding="utf-8")
    source.chmod(0o755)
    out = tmp_path / "out.tar.xz"
    bundle.write_deterministic_tar([bundle.BundleMember("run.sh", source_path=source, executable=True)], out, source_date_epoch=10)
    import tarfile
    with tarfile.open(out, "r:xz") as archive:
        member = archive.getmember("run.sh")
        assert member.mode == 0o755 and archive.extractfile(member).read() == b"#!/bin/sh\n"


def test_release_variant_dev_publish_reports_assets_without_immutable_tag(tmp_path):
    artifact = tmp_path / "demo.whl"; artifact.write_bytes(b"wheel")
    extra = tmp_path / "manifest.json"; extra.write_text("{}\n")
    calls = []
    gh = SimpleNamespace(publish=lambda *args, **kwargs: calls.append((args, kwargs)), asset_download_url=lambda *a: "url")
    result = release.publish_versioned_variants(
        gh, prefix="demo", version="1.0.0.dev1",
        variants=[release.VariantArtifact("py311", artifact, (extra,))], asset_suffix=".whl",
    )
    assert result["release_tag"] is None
    assert calls[0][0][0] == "demo-latest"
    assert any(path.name.endswith(".sha256") for path in artifact.parent.iterdir())


def test_release_variant_latest_manifest_contains_hash_and_label(tmp_path):
    artifact = tmp_path / "demo.whl"; artifact.write_bytes(b"wheel")
    calls = []
    gh = SimpleNamespace(publish=lambda *args, **kwargs: calls.append((args, kwargs)), asset_download_url=lambda tag, name: f"https://download/{name}")
    result = release.publish_versioned_variants(
        gh, prefix="demo", version="1.0.0", variants=[release.VariantArtifact("py311", artifact, label="CPython 3.11")], asset_suffix=".whl",
    )
    manifest = artifact.with_name("latest.json")
    data = json.loads(manifest.read_text())
    assert result["release_tag"] == "demo-v1.0.0"
    assert data["variants"][0]["label"] == "CPython 3.11"
    assert data["variants"][0]["sha256"] == result["variants"][0]["sha256"]
    assert calls[-1][0][0] == "demo-latest"


def test_bundle_member_rejects_missing_source_and_content():
    with pytest.raises(ValueError, match="either source_path or content"):
        bundle.BundleMember("empty")


def test_agent_cli_parser_rejects_unknown_scope():
    with pytest.raises(SystemExit):
        agent_cli._build_parser().parse_args(["--scope", "invalid", "status"])
