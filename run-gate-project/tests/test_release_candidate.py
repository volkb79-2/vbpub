"""Behavioral checks for the exact-wheel release handshake."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location(
    "release_candidate", TOOLS / "release_candidate.py"
)
assert SPEC is not None and SPEC.loader is not None
release_candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_candidate)


def test_prepared_release_version_uses_first_dated_stable_section(tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n\nPending.\n\n"
        "<!-- cmru: release history -->\n\n"
        "## [24.0.0] - 2026-10-14\n\nCandidate.\n\n"
        "## [23.10.0] - 2026-10-07\n\nReleased.\n",
        encoding="utf-8",
    )

    source_entry = str(release_candidate.REPOSITORY_ROOT / "cmru" / "src")
    was_present = source_entry in sys.path
    assert release_candidate.prepared_release_version(changes) == "24.0.0"
    assert (source_entry in sys.path) is was_present


def test_prepared_release_version_refuses_unreleased_only_changelog(tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("# Changelog\n\n## [Unreleased]\n\nPending.\n", encoding="utf-8")

    with pytest.raises(release_candidate.ReleaseCandidateError, match="no dated stable release section"):
        release_candidate.prepared_release_version(changes)


def test_prepared_release_version_refuses_an_unrelated_installed_cmru(monkeypatch, tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("## [24.0.0] - 2026-10-14\n\nCandidate.\n", encoding="utf-8")
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    parser = tmp_path / "elsewhere.py"
    parser.write_text("", encoding="utf-8")
    fake_cmru = type(sys)("cmru")
    fake_cmru.__path__ = []
    fake_notes = type(sys)("cmru.release_notes")
    fake_notes.__file__ = str(parser)
    fake_notes.markdown_h2_headings = lambda _markdown: ()
    monkeypatch.setitem(sys.modules, "cmru", fake_cmru)
    monkeypatch.setitem(sys.modules, "cmru.release_notes", fake_notes)

    with pytest.raises(release_candidate.ReleaseCandidateError, match="outside the selected checkout"):
        release_candidate.prepared_release_version(changes)


def test_wheel_identity_must_match_both_version_and_digest():
    expected = {"version": "24.0.0", "sha256": "a" * 64}
    release_candidate._require_same_wheel(expected.copy(), expected, "rebuilt wheel")

    with pytest.raises(release_candidate.ReleaseCandidateError, match="wheel SHA-256"):
        release_candidate._require_same_wheel(
            {"version": "24.0.0", "sha256": "b" * 64}, expected, "rebuilt wheel"
        )


def test_receipt_requires_a_candidate_commit_and_wheel_digest(tmp_path):
    path = tmp_path / "receipt.json"
    valid = {
        "schema_version": 1,
        "candidate_commit": "1" * 40,
        "version": "24.0.0",
        "filename": "run_gate-24.0.0-py3-none-any.whl",
        "sha256": "a" * 64,
        "image_tag": "tester-unified:local",
        "image_id": "sha256:" + "b" * 64,
    }
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert release_candidate._read_receipt(path) == valid

    valid["sha256"] = "not-a-digest"
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="malformed SHA-256"):
        release_candidate._read_receipt(path)

    valid["sha256"] = "a" * 64
    valid["schema_version"] = True
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt .* is malformed"):
        release_candidate._read_receipt(path)

    valid["schema_version"] = 1
    valid["image_id"] = "sha256:not-an-image-id"
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="malformed image ID"):
        release_candidate._read_receipt(path)


def test_running_image_identity_must_match_receipted_candidate(monkeypatch, tmp_path):
    receipt = {
        "schema_version": 1,
        "candidate_commit": "1" * 40,
        "version": "24.0.0",
        "filename": "run_gate-24.0.0-py3-none-any.whl",
        "sha256": "a" * 64,
        "image_tag": "tester-unified:local",
        "image_id": "sha256:" + "b" * 64,
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    identity_path = tmp_path / "image-identity.json"
    identity_path.write_text(json.dumps({"version": "24.0.0", "sha256": "a" * 64}), encoding="utf-8")

    monkeypatch.setattr(release_candidate, "_git", lambda *_args: receipt["candidate_commit"])
    monkeypatch.setattr(release_candidate, "IMAGE_IDENTITY_PATH", identity_path)
    release_candidate.verify_running_image(receipt_path)

    identity_path.write_text(json.dumps({"version": "24.0.0", "sha256": "c" * 64}), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="running tester image wheel SHA-256"):
        release_candidate.verify_running_image(receipt_path)


def test_release_image_builder_checks_governed_parent_and_exact_labels(tmp_path):
    builder = (TOOLS.parents[1] / "tester-unified" / "build-image.sh").read_text(encoding="utf-8")
    dockerfile = (TOOLS.parents[1] / "tester-unified" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CGROUP_PARENT_DEV_GATES or --cgroup-parent is required' in builder
    assert 'cmru tester-gate --cwd run-gate-project --cgroup-parent "$cgroup_parent" /bin/true' in builder
    assert 'docker buildx build --load --cgroup-parent "$cgroup_parent"' in builder
    assert 'org.vbpub.run-gate.wheel-sha256' in builder
    assert 'org.vbpub.run-gate.version' in dockerfile
    assert '/etc/tester-unified-run-gate-wheel.json' in dockerfile


def test_cmru_release_gate_builds_before_testing_and_verifies_after_build():
    cmru = (TOOLS.parents[0] / "cmru.toml").read_text(encoding="utf-8")
    assert cmru.index('"tools/release_candidate.py", "prepare"') < cmru.index('"run-gate", "selftest"')
    assert '"tools/release_candidate.py", "verify"' in cmru
    assert 'RUN_GATE_RELEASE_WHEEL_RECEIPT=.assay/run-gate-release-wheel.json' in cmru
