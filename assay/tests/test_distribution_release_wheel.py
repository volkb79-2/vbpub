"""Ordinary regression coverage for `gate/distribution/release_wheel.py`.

P24 (A-198-A-201): the locked, carver-owned acceptance suite at
`nyxloom-trove/carve-assets/P24/test_acceptance.py` is the authoritative,
24-case proof of this helper's contract, but it lives outside `tests/` and is
never collected by the registered gate (`tools/tester-unified-gate.sh` runs
`pytest tests -q ...`). This file promotes a representative slice of those
locked attacks into the ordinary, tracked test tree so the registered gate
itself exercises `release_wheel.py` — hostile manifest shapes, hostile ZIP/
METADATA shapes, unsafe file kinds, and the pip-hash-mode check/use race.

Like the locked suite, these tests drive the helper only as a subprocess
(never `import release_wheel`, and never `import assay`) and compare against
independent, hand-computed expectations — never against anything the helper
itself produced.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT

HELPER = PROJECT_ROOT / "gate" / "distribution" / "release_wheel.py"
ASSET_ROOT = PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P24"
FIXTURE_WHEEL = ASSET_ROOT / "fixtures" / "assay-1.2.3-py3-none-any.whl"
FIXTURE_MANIFEST = ASSET_ROOT / "fixtures" / "release-manifest.json"
RELEASE_SHA256 = "fdec65b2c944de61cfbfb0e0672f96136becc2c42db88c131d70ea19383a7578"


def run_helper(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    assert HELPER.is_file(), f"P24 helper is absent: {HELPER}"
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True,
        text=True,
        timeout=15,  # failsafe only; no assertion depends on elapsed time
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"helper failed ({proc.returncode}): {proc.stderr}")
    return proc


def assert_refused(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr.startswith("release-wheel: REFUSED:")
    assert "Traceback" not in proc.stderr


def manifest_document(**updates: object) -> dict[str, object]:
    document = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    document.update(updates)
    return document


def write_manifest(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def test_helper_exists_and_is_the_expected_stdlib_only_module() -> None:
    assert HELPER.is_file()
    source = HELPER.read_text(encoding="utf-8")
    assert "NotImplementedError" not in source, "a P24 TODO was left incomplete"
    assert "import assay" not in source, "the standalone helper must not import assay"


def test_verify_emits_exact_hash_requirement_for_the_real_positive_fixture() -> None:
    proc = run_helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(FIXTURE_MANIFEST))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == (
        f"assay @ {FIXTURE_WHEEL.absolute().as_uri()} --hash=sha256:{RELEASE_SHA256}\n"
    )


def test_manifest_command_recreates_the_locked_document_and_refuses_a_second_write(
    tmp_path: Path,
) -> None:
    output = tmp_path / "release-manifest.json"
    proc = run_helper(
        "manifest", "--wheel", str(FIXTURE_WHEEL), "--version", "1.2.3", "--output", str(output)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == proc.stderr == ""
    assert output.read_bytes() == FIXTURE_MANIFEST.read_bytes()
    # check/use: a second `manifest` run must not silently overwrite an
    # existing output — it must refuse.
    assert_refused(
        run_helper(
            "manifest", "--wheel", str(FIXTURE_WHEEL), "--version", "1.2.3", "--output", str(output)
        )
    )


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 1, "filename": "assay-1.2.3-py3-none-any.whl", "version": "1.2.3"},
        {**manifest_document(), "extra": 1},
        {**manifest_document(), "schema_version": 2},
        {**manifest_document(), "version": "1.2"},
        {**manifest_document(), "version": "01.2.3"},
        {**manifest_document(), "sha256": "not-hex"},
    ],
)
def test_verify_refuses_each_hostile_manifest_shape(tmp_path: Path, document: object) -> None:
    candidate = write_manifest(tmp_path / "manifest.json", document)
    assert_refused(run_helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(candidate)))


def test_verify_refuses_a_duplicate_manifest_member(tmp_path: Path) -> None:
    candidate = tmp_path / "manifest.json"
    candidate.write_text(
        '{"schema_version":1,"schema_version":1,'
        '"filename":"assay-1.2.3-py3-none-any.whl","version":"1.2.3",'
        f'"sha256":"{RELEASE_SHA256}"}}\n',
        encoding="utf-8",
    )
    assert_refused(run_helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(candidate)))


def test_verify_refuses_wrong_basename_and_wrong_hash_independently(tmp_path: Path) -> None:
    renamed = tmp_path / "renamed.whl"
    shutil.copyfile(FIXTURE_WHEEL, renamed)
    assert_refused(run_helper("verify", "--wheel", str(renamed), "--manifest", str(FIXTURE_MANIFEST)))

    wrong_hash = write_manifest(tmp_path / "wrong-hash.json", manifest_document(sha256="1" * 64))
    assert_refused(run_helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(wrong_hash)))


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("mutation", ["name", "version", "duplicate"])
def test_verify_refuses_metadata_mismatches_after_a_correct_hash(
    tmp_path: Path, mutation: str
) -> None:
    """A hash-only verifier would pass a self-consistent WRONG wheel."""
    target = tmp_path / FIXTURE_WHEEL.name
    with zipfile.ZipFile(FIXTURE_WHEEL) as archive:
        metadata_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        original = archive.read(metadata_name)
    if mutation == "name":
        replacement = original.replace(b"Name: assay\n", b"Name: other\n", 1)
    elif mutation == "version":
        replacement = original.replace(b"Version: 1.2.3\n", b"Version: 9.9.9\n", 1)
    else:
        replacement = original

    with zipfile.ZipFile(FIXTURE_WHEEL) as incoming, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED
    ) as outgoing:
        for info in incoming.infolist():
            data = incoming.read(info)
            if info.filename == metadata_name:
                data = replacement
            outgoing.writestr(info, data)
        if mutation == "duplicate":
            outgoing.writestr(metadata_name, replacement)

    manifest = write_manifest(
        tmp_path / "manifest.json",
        manifest_document(sha256=hashlib.sha256(target.read_bytes()).hexdigest()),
    )
    assert_refused(run_helper("verify", "--wheel", str(target), "--manifest", str(manifest)))


def test_verify_refuses_a_wheel_with_a_wrong_dist_info_path(tmp_path: Path) -> None:
    """The METADATA member's own path, not only its content, must be exact."""
    target = tmp_path / FIXTURE_WHEEL.name
    with zipfile.ZipFile(FIXTURE_WHEEL) as incoming, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED
    ) as outgoing:
        for info in incoming.infolist():
            data = incoming.read(info)
            name = info.filename
            if name.endswith(".dist-info/METADATA"):
                name = "other-1.2.3.dist-info/METADATA"
            outgoing.writestr(name, data)
    manifest = write_manifest(
        tmp_path / "manifest.json",
        manifest_document(sha256=hashlib.sha256(target.read_bytes()).hexdigest()),
    )
    assert_refused(run_helper("verify", "--wheel", str(target), "--manifest", str(manifest)))


def test_verify_refuses_a_corrupt_zip(tmp_path: Path) -> None:
    target = tmp_path / FIXTURE_WHEEL.name
    target.write_bytes(b"not a zip file at all")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        manifest_document(sha256=hashlib.sha256(target.read_bytes()).hexdigest()),
    )
    assert_refused(run_helper("verify", "--wheel", str(target), "--manifest", str(manifest)))


def test_verify_refuses_oversized_and_symlinked_manifests_without_reading_the_wheel(
    tmp_path: Path,
) -> None:
    oversized = tmp_path / "manifest.json"
    oversized.write_bytes(b" " * 4097)
    assert_refused(run_helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(oversized)))

    linked = tmp_path / "link.json"
    linked.symlink_to(FIXTURE_MANIFEST)
    assert_refused(run_helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(linked)))


def test_verify_refuses_a_symlinked_wheel(tmp_path: Path) -> None:
    linked_wheel = tmp_path / FIXTURE_WHEEL.name
    linked_wheel.symlink_to(FIXTURE_WHEEL)
    assert_refused(
        run_helper("verify", "--wheel", str(linked_wheel), "--manifest", str(FIXTURE_MANIFEST))
    )


def test_verify_refuses_a_fifo_in_place_of_a_wheel(tmp_path: Path) -> None:
    fifo = tmp_path / FIXTURE_WHEEL.name
    os.mkfifo(fifo)
    assert_refused(run_helper("verify", "--wheel", str(fifo), "--manifest", str(FIXTURE_MANIFEST)))


def test_verify_refuses_an_oversized_wheel_without_reading_all_of_it(tmp_path: Path) -> None:
    huge = tmp_path / FIXTURE_WHEEL.name
    with huge.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    assert_refused(run_helper("verify", "--wheel", str(huge), "--manifest", str(FIXTURE_MANIFEST)))


def test_pip_require_hashes_rechecks_bytes_mutated_after_a_successful_verify(
    tmp_path: Path,
) -> None:
    """Verification is not installation: pip must recheck the bytes it opens."""
    raced_dir = tmp_path / "raced"
    raced_dir.mkdir()
    raced_wheel = raced_dir / FIXTURE_WHEEL.name
    shutil.copyfile(FIXTURE_WHEEL, raced_wheel)
    raced_manifest = write_manifest(raced_dir / "manifest.json", manifest_document())

    before_race = run_helper("verify", "--wheel", str(raced_wheel), "--manifest", str(raced_manifest))
    assert before_race.returncode == 0, before_race.stderr

    raced_wheel.write_bytes(raced_wheel.read_bytes() + b"changed-after-verification")
    requirements = raced_dir / "verified-requirement.txt"
    requirements.write_text(before_race.stdout, encoding="utf-8")

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=60)
    refused = subprocess.run(
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert refused.returncode != 0
    assert "THESE PACKAGES DO NOT MATCH THE HASHES" in refused.stderr


# --- reviewer combined-axis attack: correct hash x undecodable METADATA -------
#
# The mutations below corrupt only the METADATA member's STORAGE (its
# compression method, encryption flag, or compressed payload), never the
# wheel's identity as a byte string -- the manifest is then written for the
# mutated file, so the sha256 check PASSES and the ZIP decode is the only
# thing left to refuse. A verifier that reaches the decoder without
# translating its failures returns 1 with a traceback instead of the
# contract's `2` + `release-wheel: REFUSED:`, which is a crash wearing a
# refusal's clothes: a consumer that branches on the exit status cannot tell
# "this wheel is bad" from "the verifier broke".


def _find_member_headers(raw: bytearray, member: bytes) -> tuple[int, int]:
    """Return (central-directory offset, local-header offset) for *member*."""
    cursor = 0
    while True:
        cursor = raw.find(b"PK\x01\x02", cursor)
        assert cursor >= 0, f"no central-directory entry for {member!r}"
        name_length = struct.unpack_from("<H", raw, cursor + 28)[0]
        if bytes(raw[cursor + 46 : cursor + 46 + name_length]) == member:
            local = struct.unpack_from("<I", raw, cursor + 42)[0]
            assert raw[local : local + 4] == b"PK\x03\x04", "bad local header"
            return cursor, local
        cursor += 4


def _wheel_with_undecodable_metadata(source: Path, target: Path, mutation: str) -> None:
    with zipfile.ZipFile(source) as archive:
        member = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        members = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as outgoing:
        for info, data in members:
            outgoing.writestr(info, data)

    raw = bytearray(target.read_bytes())
    central, local = _find_member_headers(raw, member.encode())
    if mutation == "unsupported-compression":
        struct.pack_into("<H", raw, central + 10, 99)
        struct.pack_into("<H", raw, local + 8, 99)
    elif mutation == "encrypted":
        for offset, flag_at in ((central, 8), (local, 6)):
            flags = struct.unpack_from("<H", raw, offset + flag_at)[0]
            struct.pack_into("<H", raw, offset + flag_at, flags | 0x1)
    else:  # corrupt-payload: keep a real deflate prefix, destroy the remainder
        # Zeroing the TAIL (rather than substituting a whole new stream) makes
        # the decompressor itself fail on an invalid code, which is a distinct
        # failure class from the CRC mismatch a wholesale replacement produces.
        compressed_size = struct.unpack_from("<I", raw, local + 18)[0]
        name_length = struct.unpack_from("<H", raw, local + 26)[0]
        extra_length = struct.unpack_from("<H", raw, local + 28)[0]
        start = local + 30 + name_length + extra_length
        keep = min(10, compressed_size)
        raw[start + keep : start + compressed_size] = b"\x00" * (compressed_size - keep)
    target.write_bytes(bytes(raw))


@pytest.mark.parametrize(
    "mutation", ["unsupported-compression", "encrypted", "corrupt-payload"]
)
def test_verify_refuses_an_undecodable_metadata_member_after_a_correct_hash(
    tmp_path: Path, mutation: str
) -> None:
    target = tmp_path / FIXTURE_WHEEL.name
    _wheel_with_undecodable_metadata(FIXTURE_WHEEL, target, mutation)

    # The hash is computed over the MUTATED file: the manifest is satisfied and
    # only the METADATA decode can refuse.
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = write_manifest(tmp_path / "manifest.json", manifest_document(sha256=digest))

    assert_refused(run_helper("verify", "--wheel", str(target), "--manifest", str(manifest)))
    # `manifest` reads the same member, so it must refuse on the same input.
    assert_refused(
        run_helper(
            "manifest",
            "--wheel",
            str(target),
            "--version",
            "1.2.3",
            "--output",
            str(tmp_path / "out.json"),
        )
    )
