from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

HOSTOBS_ROOT = Path(os.environ["HOSTOBS_ROOT"])
_CACHE_MODULE = HOSTOBS_ROOT / "tools" / "verify-crate-cache.py"
_CACHE_SPEC = importlib.util.spec_from_file_location("verify_crate_cache", _CACHE_MODULE)
if _CACHE_SPEC is None or _CACHE_SPEC.loader is None:
    raise RuntimeError(f"cannot load cache guard module at {_CACHE_MODULE}")
verify_crate_cache = importlib.util.module_from_spec(_CACHE_SPEC)
sys.modules[_CACHE_SPEC.name] = verify_crate_cache
_CACHE_SPEC.loader.exec_module(verify_crate_cache)


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

    def test_missing_locked_archive_entry_is_named(self) -> None:
        other = self.cache / "other-crate-0.1.0.crate"
        other.write_bytes(b"other crate bytes")
        digest = hashlib.sha256(other.read_bytes()).hexdigest()
        self.manifest.write_text(f"{digest}  {other}\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "missing locked archive.*sample-crate"):
            self.verify()

    def test_changed_cached_archive_is_refused(self) -> None:
        self.archive.write_bytes(self.archive_bytes + b"tamper")
        # Keep the separate image-lock identity guard from masking this archive oracle.
        self.baked.write_text("0" * 64 + "\n", encoding="ascii")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "checksum mismatch.*sample-crate"):
            self.verify()

    def test_lock_checksum_must_equal_manifest_checksum(self) -> None:
        self.write_lock("0" * 64)
        # The lock checksum should reject this before the independent baked-lock check.
        self.baked.write_text("0" * 64 + "\n", encoding="ascii")
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
        self.write_baked_lock_digest()
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "unsupported source"):
            self.verify()

    def test_lock_package_requires_name_and_version(self) -> None:
        self.lock.write_text(
            "version = 4\n\n[[package]]\n"
            'version = "1.2.3"\n'
            f'source = "{REGISTRY}"\n'
            f'checksum = "{self.archive_digest}"\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "without name/version"):
            self.verify()

    def test_lock_checksum_must_be_sha256(self) -> None:
        self.write_lock("not-a-digest")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "no valid archive checksum"):
            self.verify()

    def test_malformed_lock_is_refused(self) -> None:
        self.lock.write_text("this is not Cargo.lock TOML\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "cannot read Cargo.lock"):
            self.verify()

    def test_missing_cache_manifest_is_refused(self) -> None:
        self.manifest.unlink()
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "cannot read crate cache manifest"):
            self.verify()

    def test_missing_baked_lock_identity_is_refused(self) -> None:
        self.baked.unlink()
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "identity is missing or unreadable"):
            self.verify()

    def test_malformed_baked_lock_identity_is_refused(self) -> None:
        self.baked.write_text("not-a-digest\n", encoding="ascii")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "identity is malformed"):
            self.verify()

    def test_malformed_manifest_is_refused(self) -> None:
        self.manifest.write_text("not a checksum row\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "malformed crate cache manifest"):
            self.verify()

    def test_manifest_checksum_must_be_sha256(self) -> None:
        self.manifest.write_text(f"not-a-digest  {self.archive}\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "malformed crate cache manifest"):
            self.verify()

    def test_manifest_path_must_not_be_empty(self) -> None:
        self.manifest.write_text(f"{self.archive_digest}  \n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "malformed crate cache manifest"):
            self.verify()

    def test_empty_manifest_is_refused(self) -> None:
        self.manifest.write_text("  \n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "manifest is empty"):
            self.verify()

    def test_manifest_path_must_be_absolute(self) -> None:
        self.manifest.write_text(f"{self.archive_digest}  relative.crate\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "path is not absolute"):
            self.verify()

    def test_manifest_path_must_stay_inside_cargo_home(self) -> None:
        outside = self.root / "outside.crate"
        outside.write_bytes(self.archive_bytes)
        self.write_manifest(self.archive_digest)
        self.manifest.write_text(f"{self.archive_digest}  {outside}\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "escapes CARGO_HOME"):
            self.verify()

    def test_manifest_entry_must_name_a_crate_archive(self) -> None:
        wrong_type = self.cache / "sample-crate-1.2.3.zip"
        self.manifest.write_text(f"{self.archive_digest}  {wrong_type}\n", encoding="utf-8")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "not a .crate archive"):
            self.verify()

    def test_manifest_cannot_repeat_an_archive(self) -> None:
        self.manifest.write_text(
            f"{self.archive_digest}  {self.archive}\n{self.archive_digest}  {self.archive}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "repeats archive"):
            self.verify()

    def test_lock_cannot_map_multiple_packages_to_one_archive(self) -> None:
        self.lock.write_text(
            "version = 4\n\n"
            "[[package]]\n"
            'name = "sample-crate"\n'
            'version = "1.2.3"\n'
            f'source = "{REGISTRY}"\n'
            f'checksum = "{self.archive_digest}"\n\n'
            "[[package]]\n"
            'name = "sample-crate"\n'
            'version = "1.2.3"\n'
            f'source = "{REGISTRY}"\n'
            f'checksum = "{self.archive_digest}"\n',
            encoding="utf-8",
        )
        self.write_baked_lock_digest()
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "more than one package"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
