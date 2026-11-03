from __future__ import annotations

import json
from pathlib import Path

import pytest

from observed_patch_manifest import (
    EXPECTED_FILES,
    ObservedPatchManifestError,
    load_observed_patch_manifest,
    verify_observed_patch_files,
)


MANIFEST = Path(__file__).parents[1] / "v27" / "observed-v1001-unrecorded-code-20261021.json"


def load_manifest(path: Path = MANIFEST) -> dict:
    return load_observed_patch_manifest(
        path,
        target_host="v1001.vxxu.de",
        expected_version="27.0.0-RC.1",
        profile="path-a",
    )


def test_observed_v1001_manifest_pins_exact_target_and_all_adapter_files() -> None:
    manifest = load_manifest()
    assert manifest["install_record"] == "absent"
    assert set(manifest["files"]) == EXPECTED_FILES
    assert len(manifest["files"]) == 17


@pytest.mark.parametrize(
    ("target_host", "expected_version", "profile", "message"),
    [
        ("other.example", "27.0.0-RC.1", "path-a", "different target host"),
        ("v1001.vxxu.de", "28.0.0-MASTER", "path-a", "expected v27 build"),
        ("v1001.vxxu.de", "27.0.0-RC.1", "path-b", "Path A profile"),
    ],
)
def test_manifest_rejects_wrong_target_release_or_storage_profile(
    target_host: str, expected_version: str, profile: str, message: str,
) -> None:
    with pytest.raises(ObservedPatchManifestError, match=message):
        load_observed_patch_manifest(
            MANIFEST,
            target_host=target_host,
            expected_version=expected_version,
            profile=profile,
        )


@pytest.mark.parametrize("corruption", ["missing_file", "extra_file", "bad_digest", "unsafe_path"])
def test_manifest_rejects_file_set_and_path_corruption(tmp_path: Path, corruption: str) -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if corruption == "missing_file":
        document["files"].pop(next(iter(document["files"])))
    elif corruption == "extra_file":
        document["files"]["../outside.py"] = "0" * 64
    elif corruption == "bad_digest":
        document["files"][next(iter(document["files"]))] = "not-a-digest"
    else:
        digest = document["files"].pop(next(iter(document["files"])))
        document["files"]["/absolute/path.py"] = digest
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ObservedPatchManifestError):
        load_manifest(path)


class FakeTarget:
    def __init__(self, *, bad_digest: bool = False) -> None:
        self.bad_digest = bad_digest
        self.argv: list[str] | None = None

    def run(self, argv: list[str], *, timeout: int) -> str:
        assert timeout == 120
        self.argv = argv
        paths = argv[2:]
        lines = []
        for index, remote_path in enumerate(paths):
            name = remote_path.removeprefix("/usr/lib/python3/dist-packages/")
            digest = load_manifest()["files"][name]
            if self.bad_digest and index == 0:
                digest = "0" * 64
            lines.append(f"{digest}  {remote_path}")
        return "\n".join(lines)


def test_remote_observed_code_fingerprints_are_checked_exactly() -> None:
    manifest = load_manifest()
    target = FakeTarget()
    verify_observed_patch_files(target, manifest)
    assert target.argv is not None
    assert target.argv[:2] == ["sha256sum", "--"]

    with pytest.raises(ObservedPatchManifestError, match="no longer matches"):
        verify_observed_patch_files(FakeTarget(bad_digest=True), manifest)
