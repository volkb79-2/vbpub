# cmru backlog: KI-208
#
# This is a local workaround for a cmru gap: bundle-build's generic manifest
# does not carry the Seam-3 per-distribution entries that cmru's own installer
# core verifies for declared [[project.installer.wheels]]. The same gap is why a
# 3.x instance cannot use its retained installer to adopt a 4.0 bundle.
#
# The extension validates the rendered wheel allowlist against cmru's generic
# files map and retains the installer's extra-wheel refusal. When cmru owns the
# per-distribution manifest contract, remove this compatibility shim.
"""Installer extension: validate the rendered wheel allowlist against cmru's manifest.

cmru's generic bundle manifest lists every file in ``manifest["files"]``. The
installer still carries the closed WHEEL_SPECS allowlist rendered from
``[[project.installer.wheels]]``; this fragment makes wheel verification use that
allowlist plus the generic per-file hashes, and refuses any extra wheel.
"""

import hashlib


def _bundle_contract_entry(wheel_path, manifest, distribution):
    files = manifest.get("files")
    if not isinstance(files, dict):
        fatal(f"manifest.json has no files map for wheel {distribution!r}.", EXIT_FAIL)
    rel = f"wheels/{wheel_path.name}"
    entry = files.get(rel)
    if not isinstance(entry, dict):
        fatal(f"wheel {rel!r} ({distribution}) is not covered by manifest.json.", EXIT_FAIL)
    expected = entry.get("sha256")
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(char not in "0123456789abcdef" for char in expected.lower())
    ):
        fatal(f"manifest.json has an invalid sha256 for wheel {rel!r}.", EXIT_FAIL)
    if entry.get("size") != wheel_path.stat().st_size:
        fatal(f"manifest.json size differs for wheel {rel!r}.", EXIT_FAIL)
    actual = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    if actual != expected.lower():
        fatal(
            f"Wheel SHA256 mismatch for {wheel_path.name} (distribution: {distribution}):\n"
            f"  expected: {expected}\n"
            f"  got:      {actual}",
            EXIT_FAIL,
        )
    ok(f"Wheel {wheel_path.name} ({distribution}) sha256 verified.")
    return expected.lower()


def _bundle_contract_verify_wheel(wheel_path, manifest, distribution):
    return _bundle_contract_entry(wheel_path, manifest, distribution)


_CORE_INSTALL_WHEELS = globals()["_install_wheels"]
globals()["_verify_wheel_sha256"] = _bundle_contract_verify_wheel


def _bundle_contract_install_wheels(tree, release, manifest):
    extras = []
    wheel_listed = globals()["_wheel_listed"]
    for wheel in sorted(tree.glob("wheels/*.whl")):
        rel = wheel.relative_to(tree).as_posix()
        if not wheel_listed(rel):
            extras.append(rel)
    if extras:
        fatal(
            "bundle contains wheel(s) absent from [[project.installer.wheels]]: "
            + ", ".join(extras),
            EXIT_FAIL,
        )
    return _CORE_INSTALL_WHEELS(tree, release, manifest)


globals()["_install_wheels"] = _bundle_contract_install_wheels


def _bundle_contract_register(subparsers):
    return {}


_EXTENSIONS.append(_bundle_contract_register)
