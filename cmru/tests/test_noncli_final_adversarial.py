"""Behavioural witnesses for remaining non-CLI CMRU public seams."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


class TestResolveAndManifest:
    def test_latest_json_success_and_fallback(self, monkeypatch):
        import cmru.resolve as resolve
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"version":"1.2.3","tag":"x-v1.2.3","url":"u","sha256":"s"}'
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
        hit = resolve.resolve_via_latest_json("https://github/x", "x-v")
        assert hit["version"] == "1.2.3"
        host = SimpleNamespace(resolve_latest=lambda prefix: {"version": "2"})
        assert resolve.resolve(host, "x-v", use_latest_json=False) == {"version": "2"}

    def test_format_result_contracts(self):
        from cmru.resolve import format_result
        result = {"version": "1", "tag": "tls-edge-v1", "url": "u", "sha256": "s"}
        assert format_result(result, "url") == "u"
        assert "TLS_EDGE_VERSION=1" in format_result(result, "env")
        assert json.loads(format_result(result, "json"))["tag"] == "tls-edge-v1"

    def test_manifest_requires_epoch_and_valid_images(self, tmp_path, monkeypatch):
        from cmru.manifest import build_manifest
        a = tmp_path / "cmru-1.0-py3.whl"; b = tmp_path / "ciu-2.0-py3.whl"
        a.write_bytes(b"a"); b.write_bytes(b"b")
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
        kwargs = dict(project="p", tag="p-v1", source_commit="abc", cmru_wheel=a, ciu_wheel=b, images=None, installer_schema_version=1, host_config_schema_version=1, platform={}, upgrade={})
        with pytest.raises(RuntimeError): build_manifest(**kwargs)
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        with pytest.raises(ValueError): build_manifest(**{**kwargs, "images": {}})
        m = build_manifest(**kwargs)
        assert m["created"] == "2023-11-14T22:13:20Z"


class TestDelegatedExternalSeams:
    @pytest.mark.parametrize("fn,tool", [("cosign_sign", "cosign"), ("syft_sbom", "syft"), ("grype_scan", "grype"), ("git_cliff_changelog", "git-cliff"), ("nfpm_package", "nfpm"), ("minisign_sign", "minisign")])
    def test_missing_external_tool_refuses(self, monkeypatch, tmp_path, fn, tool):
        import cmru.delegated as d
        monkeypatch.setattr(d, "_which", lambda name: None)
        with pytest.raises(SystemExit):
            if fn == "cosign_sign": d.cosign_sign(tmp_path / "a")
            elif fn == "syft_sbom": d.syft_sbom(tmp_path / "a", tmp_path / "o")
            elif fn == "grype_scan": d.grype_scan(tmp_path / "a")
            elif fn == "git_cliff_changelog": d.git_cliff_changelog(tmp_path / "o")
            elif fn == "nfpm_package": d.nfpm_package(tmp_path / "c", tmp_path)
            else: d.minisign_sign(tmp_path / "a", secret_key="k", trusted_comment="t")

    def test_minisign_verify_distinguishes_success_and_failure(self, monkeypatch, tmp_path):
        import cmru.delegated as d
        monkeypatch.setattr(d, "_which", lambda name: "/usr/bin/minisign")
        monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stderr=b""))
        assert d.minisign_verify(tmp_path / "a", public_key="p")
        monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr=b"bad"))
        assert not d.minisign_verify(tmp_path / "a", public_key="p")


class TestProtocolAndStateBoundaries:
    def test_observed_unknown_health_round_trip_is_data_not_execution(self):
        from cmru.agent.protocol import ObservedState
        obs = ObservedState(health="unexpected", message="literal")
        restored = ObservedState.from_json(obs.to_json())
        assert restored.health == "unexpected" and restored.message == "literal"

    def test_state_generation_invalid_and_identity_malformed_are_safe(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from cmru.agent import state
        state.ensure_state_dir()
        (state.state_dir() / "current_generation").write_text("1.2")
        (state.state_dir() / "identity.json").write_text("{")
        assert state.read_current_generation() is None
        assert state.read_identity() is None
