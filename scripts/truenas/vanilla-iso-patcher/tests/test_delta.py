from __future__ import annotations

import hashlib
from pathlib import Path
import sys

import pytest

PATCHER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCHER_ROOT))
import delta  # noqa: E402


def _install_fake_xdelta(monkeypatch, *, mutate_source_during_encode=False):
    monkeypatch.setattr(delta, "resolve_xdelta", lambda _path: ("/fake/xdelta3", "3.2.2"))

    def run(argv, *, phase):
        if phase == "encoding":
            source = Path(argv[argv.index("-s") + 1])
            target = Path(argv[-2])
            destination = Path(argv[-1])
            destination.write_bytes(target.read_bytes())
            if mutate_source_during_encode:
                source.write_bytes(b"changed during encode")
            return
        if phase == "verification decode":
            patch = Path(argv[-2])
            destination = Path(argv[-1])
            destination.write_bytes(patch.read_bytes())
            return
        raise AssertionError(f"unexpected xdelta phase {phase!r}")

    monkeypatch.setattr(delta, "_run", run)


def test_delta_is_published_only_after_reconstruction_matches_source_and_target(tmp_path, monkeypatch):
    source = tmp_path / "vanilla.iso"
    target = tmp_path / "patched.iso"
    output = tmp_path / "patched.vcdiff"
    source.write_bytes(b"vanilla ISO bytes")
    target.write_bytes(b"patched ISO bytes")
    _install_fake_xdelta(monkeypatch)

    result = delta.create_and_verify_delta(
        "/fake/xdelta3",
        source,
        target,
        output,
        expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )

    assert output.read_bytes() == target.read_bytes()
    assert result["target_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert result["delta_bytes"] == target.stat().st_size


def test_delta_refuses_a_source_that_changed_after_initial_verification(tmp_path, monkeypatch):
    source = tmp_path / "vanilla.iso"
    target = tmp_path / "patched.iso"
    output = tmp_path / "patched.vcdiff"
    source.write_bytes(b"vanilla ISO bytes")
    target.write_bytes(b"patched ISO bytes")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    _install_fake_xdelta(monkeypatch)
    source.write_bytes(b"already changed")

    with pytest.raises(delta.DeltaError, match="changed after its initial verification"):
        delta.create_and_verify_delta(
            "/fake/xdelta3", source, target, output, expected_source_sha256=expected
        )

    assert not output.exists()


def test_delta_refuses_source_mutation_during_encode(tmp_path, monkeypatch):
    source = tmp_path / "vanilla.iso"
    target = tmp_path / "patched.iso"
    output = tmp_path / "patched.vcdiff"
    source.write_bytes(b"vanilla ISO bytes")
    target.write_bytes(b"patched ISO bytes")
    _install_fake_xdelta(monkeypatch, mutate_source_during_encode=True)

    with pytest.raises(delta.DeltaError, match="changed while the delta was being verified"):
        delta.create_and_verify_delta("/fake/xdelta3", source, target, output)

    assert not output.exists()
