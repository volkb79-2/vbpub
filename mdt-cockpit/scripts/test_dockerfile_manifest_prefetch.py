"""The Dockerfile must not fetch the build's own versioned manifest: it is generated in the image and
extracted post-build, so for a new BUILD_DATE it exists neither in the context nor at the blob URL (the
20261104 release failed at `curl -f` -> 404)."""
from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _instructions() -> list[str]:
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def test_no_instruction_downloads_the_documentation_url() -> None:
    for line in _instructions():
        assert not (line.startswith("RUN") and "OCI_DOCUMENTATION" in line and "curl" in line), line


def test_no_instruction_probes_the_manifest_target_in_tmp() -> None:
    for line in _instructions():
        assert "/tmp/${PACKAGE_MANIFEST_SOURCE}" not in line, line


def test_manifest_is_still_generated_in_image() -> None:
    assert any("manifest_sections.py unified" in ln for ln in _instructions())
