from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
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
        self.baked = self.root / "hostobs-Cargo.lock.registry-sha256"
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
        digest = verify_crate_cache.registry_set_digest(self.lock)
        self.baked.write_text(digest + "\n", encoding="ascii")

    def write_lock_text(self, text: str) -> None:
        self.lock.write_text(text, encoding="utf-8")

    def lock_text_with_workspace(self, *, workspace: str = "", version: int = 4) -> str:
        return (
            f"version = {version}\n\n"
            f"{workspace}"
            "[[package]]\n"
            'name = "sample-crate"\n'
            'version = "1.2.3"\n'
            f'source = "{REGISTRY}"\n'
            f'checksum = "{self.archive_digest}"\n'
        )

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

    # --- registry-set identity (what the image cache really depends on) ---

    def test_workspace_only_lock_edit_passes_without_a_rebuild(self) -> None:
        # Only a workspace member's dependencies array and a new path package change.
        self.write_lock_text(self.lock_text_with_workspace())
        self.write_baked_lock_digest()  # the image bakes the digest of the ORIGINAL lock
        original_digest = self.baked.read_text(encoding="ascii")
        self.write_lock_text(
            self.lock_text_with_workspace(
                workspace=(
                    "[[package]]\n"
                    'name = "ws-app"\nversion = "0.1.0"\n'
                    'dependencies = [\n "sample-crate",\n "ws-lib",\n]\n\n'
                    "[[package]]\n"
                    'name = "ws-lib"\nversion = "0.1.0"\n\n'
                )
            )
        )
        self.assertEqual(verify_crate_cache.registry_set_digest(self.lock) + "\n", original_digest)
        self.assertEqual(self.verify(), ["sample-crate-1.2.3.crate"])

    def test_changed_registry_checksum_is_refused_by_the_identity_alone(self) -> None:
        # The archive, manifest and lock agree with each other; only the baked identity differs.
        changed = b"a different archive"
        self.archive.write_bytes(changed)
        digest = hashlib.sha256(changed).hexdigest()
        self.write_lock(digest)
        self.write_manifest(digest)
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "registry set differs"):
            self.verify()

    def test_added_crate_is_refused(self) -> None:
        extra = self.cache / "extra-0.1.0.crate"
        extra.write_bytes(b"extra")
        extra_digest = hashlib.sha256(extra.read_bytes()).hexdigest()
        self.write_lock_text(
            self.lock_text_with_workspace()
            + "\n[[package]]\n"
            'name = "extra"\nversion = "0.1.0"\n'
            f'source = "{REGISTRY}"\n'
            f'checksum = "{extra_digest}"\n'
        )
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "missing locked archive.*extra"):
            self.verify()
        # Even with a cache that holds it, the baked identity still refuses the new crate.
        self.manifest.write_text(
            self.manifest.read_text(encoding="utf-8") + f"{extra_digest}  {extra}\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "registry set differs"):
            self.verify()

    def test_removed_crate_is_refused(self) -> None:
        # A lock without the crate while the cache still holds it: the archive set check names it.
        self.write_lock_text("version = 4\n")
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "absent from Cargo.lock.*sample-crate"):
            self.verify()

        # An image baked from a two-crate lock; the checkout then drops one crate and the cache
        # is trimmed to match, so only the baked identity can still refuse the removal.
        second = self.cache / "zeta-0.2.0.crate"
        second.write_bytes(b"zeta")
        second_digest = hashlib.sha256(b"zeta").hexdigest()
        self.write_lock_text(
            self.lock_text_with_workspace()
            + f'\n[[package]]\nname = "zeta"\nversion = "0.2.0"\nsource = "{REGISTRY}"\n'
            f'checksum = "{second_digest}"\n'
        )
        self.write_baked_lock_digest()
        self.write_lock_text(self.lock_text_with_workspace())
        self.write_manifest(self.archive_digest)  # trimmed: the zeta archive is no longer listed
        second.unlink()
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "registry set differs"):
            self.verify()

    def test_changed_source_changes_the_digest_and_is_refused(self) -> None:
        before = verify_crate_cache.registry_set_digest(self.lock)
        self.write_lock(self.archive_digest, source="registry+https://example.invalid/index")
        self.assertNotEqual(verify_crate_cache.registry_set_digest(self.lock), before)
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "unsupported source"):
            self.verify()

    def test_changed_lock_version_is_refused_by_the_identity_alone(self) -> None:
        self.write_lock_text(self.lock_text_with_workspace(version=3))
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "registry set differs"):
            self.verify()

    def test_lock_without_an_integer_version_is_refused(self) -> None:
        self.write_lock_text(self.lock_text_with_workspace().replace("version = 4\n\n", ""))
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "integer top-level version"):
            self.verify()

    def test_package_order_does_not_change_the_digest(self) -> None:
        second = self.cache / "zeta-0.2.0.crate"
        second.write_bytes(b"zeta")
        second_digest = hashlib.sha256(b"zeta").hexdigest()
        first_block = (
            f'[[package]]\nname = "sample-crate"\nversion = "1.2.3"\nsource = "{REGISTRY}"\n'
            f'checksum = "{self.archive_digest}"\n\n'
        )
        second_block = (
            f'[[package]]\nname = "zeta"\nversion = "0.2.0"\nsource = "{REGISTRY}"\n'
            f'checksum = "{second_digest}"\n\n'
        )
        self.write_lock_text("version = 4\n\n" + first_block + second_block)
        forward = verify_crate_cache.registry_set_digest(self.lock)
        self.write_lock_text("version = 4\n\n" + second_block + first_block)
        self.assertEqual(verify_crate_cache.registry_set_digest(self.lock), forward)

    def test_git_source_is_in_the_digest_and_refused_by_the_verifier(self) -> None:
        git = "git+https://example.invalid/repo.git?rev=abc#0123456789abcdef"
        text = (
            "version = 4\n\n"
            '[[package]]\nname = "gitcrate"\nversion = "0.1.0"\n'
            f'source = "{git}"\n'
        )
        self.write_lock_text(text)
        canonical = verify_crate_cache.canonical_registry_set(tomllib.loads(text))
        self.assertEqual(canonical, f"lock-version 4\ngitcrate 0.1.0 - {git}\n")
        self.write_baked_lock_digest()
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "unsupported source"):
            self.verify()

    def test_canonical_text_has_the_agreed_shape(self) -> None:
        text = (
            "version = 4\n\n"
            '[[package]]\nname = "ws"\nversion = "0.1.0"\ndependencies = ["b"]\n\n'
            f'[[package]]\nname = "b"\nversion = "2.0.0"\nsource = "{REGISTRY}"\nchecksum = "{"1" * 64}"\n\n'
            f'[[package]]\nname = "a"\nversion = "1.0.0"\nsource = "{REGISTRY}"\nchecksum = "{"2" * 64}"\n'
        )
        self.assertEqual(
            verify_crate_cache.canonical_registry_set(tomllib.loads(text)),
            f"lock-version 4\na 1.0.0 {'2' * 64}\nb 2.0.0 {'1' * 64}\n",
        )

    def test_whitespace_in_a_canonical_field_is_refused(self) -> None:
        text = (
            "version = 4\n\n"
            f'[[package]]\nname = "a b"\nversion = "1.0.0"\nsource = "{REGISTRY}"\nchecksum = "{"2" * 64}"\n'
        )
        with self.assertRaisesRegex(verify_crate_cache.CacheMismatch, "malformed name"):
            verify_crate_cache.canonical_registry_set(tomllib.loads(text))

    def test_print_registry_digest_is_stable_and_equals_the_function(self) -> None:
        command = [
            sys.executable, "-I", str(_CACHE_MODULE), "--print-registry-digest", "--lock", str(self.lock),
        ]
        first = subprocess.run(command, capture_output=True, text=True, check=True)
        second = subprocess.run(command, capture_output=True, text=True, check=True)
        expected = verify_crate_cache.registry_set_digest(self.lock)
        self.assertEqual(first.stdout, expected + "\n")
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(first.stderr, "")
        self.assertRegex(first.stdout, r"^[0-9a-f]{64}\n$")

    def test_print_registry_digest_needs_only_the_lock_and_fails_closed(self) -> None:
        self.lock.write_text("not toml\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", str(_CACHE_MODULE), "--print-registry-digest", "--lock", str(self.lock)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("cannot read Cargo.lock", result.stderr)

    def test_verify_mode_still_requires_manifest_identity_and_cargo_home(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(_CACHE_MODULE), "--lock", str(self.lock)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--manifest is required", result.stderr)

    def test_real_hostobs_lock_digest_ignores_workspace_members(self) -> None:
        real = HOSTOBS_ROOT / "Cargo.lock"
        document = tomllib.loads(real.read_text(encoding="utf-8"))
        canonical = verify_crate_cache.canonical_registry_set(document)
        members = [p["name"] for p in document["package"] if "source" not in p]
        self.assertTrue(members)
        for name in members:
            self.assertNotIn(f"{name} ", canonical)
        self.assertTrue(canonical.startswith(f"lock-version {document['version']}\n"))

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
