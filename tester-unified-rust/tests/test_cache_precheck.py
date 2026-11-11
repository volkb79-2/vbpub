from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

HOSTOBS_ROOT = Path(os.environ["HOSTOBS_ROOT"])
sys.path.insert(0, str(HOSTOBS_ROOT / "tools"))
import verify_crate_cache  # noqa: E402


REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"


class CachePrecheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cargo_home = self.root / "cargo"
        self.cache = self.cargo_home / "registry/cache/index.crates.io-test"
        self.cache.mkdir(parents=True)
        self.lock = self.root / "Cargo.lock"
        self.manifest = self.root / "rust-crate-cache.sha256"
        self.baked = self.root / "hostobs-Cargo.lock.sha256"
        self.archive_bytes = b"crate archive bytes"
        self.archive_digest = hashlib.sha256(self.archive_bytes).hexdigest()
        self.write_lock(self.archive_digest)
        self.archive = self.cache / "sample-crate-1.2.3.crate"
        self.archive.write_bytes(self.archive_bytes)
        self.write_manifest(self.archive_digest)
        self.write_baked_lock_digest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_lock(self, checksum: str, *, source: str = REGISTRY) -> None:
        self.lock.write_text(
            "version = 4\n\n"
            "[[package]]\n"
            'name = "sample-crate"\n'
            'version = "1.2.3"\n'
            f'source = "{source}"\n'
            f'checksum = "{checksum}"\n',
            encoding="utf-8",
        )

    def write_manifest(self, checksum: str) -> None:
        self.manifest.write_text(f"{checksum}  {self.archive}\n", encoding="utf-8")

    def write_baked_lock_digest(self) -> None:
        digest = hashlib.sha256(self.lock.read_bytes()).hexdigest()
        self.baked.write_text(digest + "\n", encoding="ascii")

    def verify(self) -> list[str]:
        return verify_crate_cache.verify(self.lock, self.manifest, self.baked, self.cargo_home)

    def test_exact_lock_and_archive_pass(self) -> None:
        self.assertEqual(self.verify(), ["sample-crate-1.2.3.crate"])

    def test_missing_locked_archive_is_named(self) -> None:
        self.archive.unlink()
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "sample-crate-1.2.3.crate"):
            self.verify()

    def test_changed_cached_archive_is_refused(self) -> None:
        self.archive.write_bytes(self.archive_bytes + b"tamper")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "checksum mismatch.*sample-crate"):
            self.verify()

    def test_lock_checksum_must_equal_manifest_checksum(self) -> None:
        self.write_lock("0" * 64)
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "Cargo.lock checksum differs.*sample-crate"):
            self.verify()

    def test_lock_bytes_must_match_image_identity(self) -> None:
        self.lock.write_text(self.lock.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "differs from the lock baked"):
            self.verify()

    def test_extra_cached_archive_is_refused(self) -> None:
        extra = self.cache / "extra-0.1.0.crate"
        extra.write_bytes(b"extra")
        extra_digest = hashlib.sha256(extra.read_bytes()).hexdigest()
        self.manifest.write_text(
            self.manifest.read_text(encoding="utf-8") + f"{extra_digest}  {extra}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "absent from Cargo.lock.*extra"):
            self.verify()

    def test_non_crates_io_source_is_refused(self) -> None:
        self.write_lock(self.archive_digest, source="registry+https://example.invalid/index")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "unsupported source"):
            self.verify()

    def test_malformed_manifest_is_refused(self) -> None:
        self.manifest.write_text("not a checksum row\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "malformed crate cache manifest"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
