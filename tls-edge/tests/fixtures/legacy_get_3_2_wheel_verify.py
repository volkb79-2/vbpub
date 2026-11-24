"""The wheel-inventory verifier copied verbatim from tls-edge-v3.2.0 get.py.

The test supplies the small helper/global dependencies at call time so this
fixture remains only the historical function, not a second installer.
"""

from pathlib import Path


def _verify_wheel_sha256(wheel_path: Path, manifest: dict, distribution: str) -> str:
    """Check the wheel against manifest[distribution] (SPEC B Seam-3): a missing entry,
    a missing/invalid sha256, a wrong file name or a size/hash mismatch is EXIT_FAIL.
    Returns the expected sha256."""
    entry = manifest.get(distribution)
    if not isinstance(entry, dict):
        fatal(f"{MANIFEST_NAME} has no entry for distribution {distribution!r}.", EXIT_FAIL)
    expected = _hex_digest(entry.get("sha256"), f"distribution {distribution!r}")
    if "wheel" in entry and entry["wheel"] != wheel_path.name:
        fatal(f"{MANIFEST_NAME} lists wheel {entry['wheel']!r} for {distribution!r}, "
              f"bundle has {wheel_path.name!r}.", EXIT_FAIL)
    _check_size(entry, wheel_path.stat().st_size, f"distribution {distribution!r}")
    actual = _sha256(wheel_path)
    if actual != expected:
        fatal(
            f"Wheel SHA256 mismatch for {wheel_path.name} (distribution: {distribution!r}):\n"
            f"  expected: {expected}\n"
            f"  got:      {actual}",
            EXIT_FAIL,
        )
    ok(f"Wheel {wheel_path.name} ({distribution}) sha256 verified.")
    return expected
