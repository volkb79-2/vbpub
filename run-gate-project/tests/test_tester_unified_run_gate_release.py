"""The tester-unified image for another tool's gate carries the PUBLISHED Run-Gate wheel."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tester-unified" / "fetch-run-gate-release.py"
BUILD = REPO / "tester-unified" / "build-image.sh"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_run_gate_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fetcher(payload: bytes, sidecar: str):
    def fetch(url: str) -> bytes:
        assert url.startswith("https://github.com/volkb79-2/vbpub/releases/download/run-gate-v24.0.1/")
        return sidecar.encode() if url.endswith(".sha256") else payload
    return fetch


def test_downloads_and_verifies_the_published_wheel(tmp_path):
    module = _load()
    payload = b"wheel-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    wheel = module.fetch_release(
        "24.0.1", tmp_path / "w", _fetcher(payload, f"{digest}  run_gate-24.0.1-py3-none-any.whl\n"),
    )
    assert wheel.name == "run_gate-24.0.1-py3-none-any.whl"
    assert wheel.read_bytes() == payload


def test_digest_mismatch_writes_nothing(tmp_path):
    module = _load()
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        module.fetch_release("24.0.1", tmp_path / "w", _fetcher(b"x", "0" * 64))
    assert list((tmp_path / "w").glob("*.whl")) == []


@pytest.mark.parametrize("version", ["24.0.2.dev3+gabc", "24.0", "v24.0.1", "latest", ""])
def test_refuses_anything_but_stable_semver(tmp_path, version):
    with pytest.raises(SystemExit, match="stable SemVer"):
        _load().fetch_release(version, tmp_path / "w", _fetcher(b"", "0" * 64))


def test_refuses_a_malformed_sidecar_and_a_non_empty_destination(tmp_path):
    module = _load()
    with pytest.raises(SystemExit, match="no valid sha256"):
        module.fetch_release("24.0.1", tmp_path / "a", _fetcher(b"x", "not-a-digest"))
    dest = tmp_path / "b"
    dest.mkdir()
    (dest / "run_gate-1.0.0-py3-none-any.whl").write_bytes(b"old")
    digest = hashlib.sha256(b"x").hexdigest()
    with pytest.raises(SystemExit, match="already holds"):
        module.fetch_release("24.0.1", dest, _fetcher(b"x", digest))


def test_build_image_offers_the_flag_and_rejects_a_conflicting_wheel_dir(tmp_path):
    text = BUILD.read_text()
    assert "--run-gate-release" in text and "fetch-run-gate-release.py" in text
    result = subprocess.run(
        ["bash", str(BUILD), "--run-gate-release", "24.0.1", "--wheel-dir", str(tmp_path)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 2
    assert "mutually exclusive" in result.stderr
