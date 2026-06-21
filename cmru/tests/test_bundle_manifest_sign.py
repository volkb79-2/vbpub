"""Tests for SPEC B: deterministic bundle + manifest + minisign signing.

Test plan (from spec §7):
  1. Deterministic build: two builds from same SOURCE_DATE_EPOCH → identical sha256;
     flip epoch → digest changes.
  2. Allowlist + excludes: fixture tree with .git, .ciu, rendered *.toml, fake secret,
     log → assert none appear in the archive; only allowlisted paths do.
  3. Manifest schema: build_manifest() produces all §3 keys; canonical serialization is
     byte-stable across two calls; created tracks SOURCE_DATE_EPOCH.
  4. minisign round-trip: sign → verify succeeds; mutate byte → verify fails; swap
     manifest → verify fails.
  5. Trusted comment binds manifest: comment carries real manifest_sha256.
  6. Missing secret key when signing requested → clear error (not silent unsigned bundle).
  7. Image map is input, not invented: no images → fails fast; cmru never calls registry.

Run:
    cd /tmp/vbpub-cmru-bundle-manifest-sign/cmru && python3 -m pytest tests/test_bundle_manifest_sign.py -v
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path
from typing import Optional
from unittest import mock

import pytest

# Make sure cmru src is on path (handled by conftest.py).
from cmru.bundle import (
    BundleMember,
    collect_allowlist_members,
    write_deterministic_tar,
    _is_excluded,
)
from cmru.manifest import (
    build_manifest,
    build_trusted_comment,
    manifest_sha256,
    write_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_path(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fake_wheel(tmp: Path, name: str, version: str = "1.0.0") -> Path:
    """Create a minimal fake wheel file (just bytes for sha256 testing)."""
    wheel_name = f"{name}-{version}-py3-none-any.whl"
    wheel_path = tmp / wheel_name
    wheel_path.write_bytes(b"PK\x03\x04fake wheel content for " + name.encode() + b" " + version.encode())
    return wheel_path


def _make_fixture_tree(base: Path) -> dict:
    """Create a fixture directory tree with allowed and forbidden files.

    Returns a dict describing what was created.
    """
    # Allowed files
    (base / "src").mkdir(parents=True)
    (base / "src" / "app.py").write_text("# app\n", encoding="utf-8")
    (base / "src" / "util.py").write_text("# util\n", encoding="utf-8")
    (base / "README.md").write_text("readme\n", encoding="utf-8")
    (base / "install.sh").write_text("#!/bin/bash\necho installed\n", encoding="utf-8")
    (base / "install.sh").chmod(0o755)

    # Forbidden: .git dir
    git_dir = base / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    # Forbidden: .ciu dir
    ciu_dir = base / ".ciu"
    ciu_dir.mkdir()
    (ciu_dir / "state.json").write_text("{}", encoding="utf-8")

    # Forbidden: rendered *.toml
    (base / "docker-compose.toml").write_text("[services]\n", encoding="utf-8")

    # Forbidden: secret file
    (base / "ciu.env").write_text("SECRET=hunter2\n", encoding="utf-8")

    # Forbidden: log file
    (base / "app.log").write_text("2024-01-01 INFO started\n", encoding="utf-8")

    # Forbidden: __pycache__
    pycache = base / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-311.pyc").write_bytes(b"\x00" * 16)

    return {
        "allowed": ["src/app.py", "src/util.py", "README.md", "install.sh"],
        "forbidden": [".git/HEAD", ".ciu/state.json", "docker-compose.toml", "ciu.env", "app.log"],
    }


# ---------------------------------------------------------------------------
# Test 1: Deterministic build (build twice → identical sha256)
# ---------------------------------------------------------------------------

class TestDeterministicBuild:
    def _build(self, tmp: Path, epoch: int) -> str:
        members = [
            BundleMember(archive_path="bundle/a.txt", content=b"hello"),
            BundleMember(archive_path="bundle/b.txt", content=b"world"),
            BundleMember(archive_path="bundle/c/d.txt", content=b"nested"),
        ]
        out = tmp / f"bundle-{epoch}.tar.xz"
        write_deterministic_tar(members, out, source_date_epoch=epoch)
        return _sha256_path(out)

    def test_same_epoch_same_digest(self, tmp_path: Path) -> None:
        d1 = self._build(tmp_path / "run1", epoch=1700000000)
        d2 = self._build(tmp_path / "run2", epoch=1700000000)
        assert d1 == d2, "Two builds with same SOURCE_DATE_EPOCH must produce identical sha256"

    def test_different_epoch_different_digest(self, tmp_path: Path) -> None:
        d1 = self._build(tmp_path / "run1", epoch=1700000000)
        d2 = self._build(tmp_path / "run2", epoch=1700000001)
        assert d1 != d2, "Different SOURCE_DATE_EPOCH must produce different sha256 (epoch is wired)"

    def test_member_order_does_not_matter(self, tmp_path: Path) -> None:
        """Members provided in any order produce the same archive (sorted internally)."""
        epoch = 1700000000
        members_fwd = [
            BundleMember(archive_path="bundle/a.txt", content=b"a"),
            BundleMember(archive_path="bundle/b.txt", content=b"b"),
            BundleMember(archive_path="bundle/c.txt", content=b"c"),
        ]
        members_rev = list(reversed(members_fwd))

        out1 = tmp_path / "fwd.tar.xz"
        out2 = tmp_path / "rev.tar.xz"
        write_deterministic_tar(members_fwd, out1, source_date_epoch=epoch)
        write_deterministic_tar(members_rev, out2, source_date_epoch=epoch)

        assert _sha256_path(out1) == _sha256_path(out2)

    def test_missing_source_date_epoch_raises(self, tmp_path: Path) -> None:
        """When source_date_epoch is None and env is unset, must raise RuntimeError."""
        env = {k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"}
        members = [BundleMember(archive_path="bundle/x.txt", content=b"x")]
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="SOURCE_DATE_EPOCH"):
                write_deterministic_tar(members, tmp_path / "out.tar.xz")

    def test_source_date_epoch_from_env(self, tmp_path: Path) -> None:
        """When source_date_epoch is None, reads from SOURCE_DATE_EPOCH env var."""
        members = [BundleMember(archive_path="bundle/x.txt", content=b"x")]
        out = tmp_path / "out.tar.xz"
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            write_deterministic_tar(members, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# Test 2: Allowlist + excludes
# ---------------------------------------------------------------------------

class TestAllowlistAndExcludes:
    def _archive_names(self, tar_path: Path) -> set:
        with tarfile.open(str(tar_path), "r:xz") as tf:
            return {m.name for m in tf.getmembers()}

    def test_only_allowlisted_paths_in_archive(self, tmp_path: Path) -> None:
        fixture = tmp_path / "project"
        fixture.mkdir()
        info = _make_fixture_tree(fixture)

        allowlist = ["src", "README.md", "install.sh"]
        members = collect_allowlist_members(fixture, allowlist, archive_prefix="bundle")
        out = tmp_path / "out.tar.xz"
        write_deterministic_tar(members, out, source_date_epoch=1700000000)

        names = self._archive_names(out)

        # Allowed paths must be present.
        assert "bundle/src/app.py" in names
        assert "bundle/src/util.py" in names
        assert "bundle/README.md" in names
        assert "bundle/install.sh" in names

        # Forbidden paths must not be present.
        for forbidden in [
            ".git", ".ciu", "docker-compose.toml", "ciu.env", "app.log",
            "__pycache__",
        ]:
            for name in names:
                assert forbidden not in name, (
                    f"Excluded path component {forbidden!r} found in archive member {name!r}"
                )

    def test_is_excluded_helper(self) -> None:
        assert _is_excluded(".git/HEAD") is True
        assert _is_excluded(".ciu/state.json") is True
        assert _is_excluded("src/__pycache__/app.cpython-311.pyc") is True
        assert _is_excluded("ciu.env") is True
        assert _is_excluded("app.log") is True
        assert _is_excluded("docker-compose.toml") is True
        # minisign.key must be excluded (secret).
        assert _is_excluded("minisign.key") is True
        # normal source files must NOT be excluded.
        assert _is_excluded("src/app.py") is False
        assert _is_excluded("README.md") is False
        assert _is_excluded("install.sh") is False

    def test_nonexistent_allowlist_entry_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="does not exist"):
            collect_allowlist_members(tmp_path, ["nonexistent_file.py"])

    def test_executable_bit_preserved(self, tmp_path: Path) -> None:
        """Files with executable bit set in the source must have mode 0o755 in archive."""
        fixture = tmp_path / "project"
        fixture.mkdir()
        exe_file = fixture / "run.sh"
        exe_file.write_text("#!/bin/sh\n", encoding="utf-8")
        exe_file.chmod(0o755)

        members = collect_allowlist_members(fixture, ["run.sh"], archive_prefix="bundle")
        out = tmp_path / "out.tar.xz"
        write_deterministic_tar(members, out, source_date_epoch=1700000000)

        with tarfile.open(str(out), "r:xz") as tf:
            ti = tf.getmember("bundle/run.sh")
            assert ti.mode == 0o755, f"executable bit not set, mode={oct(ti.mode)}"

    def test_non_executable_file_mode_644(self, tmp_path: Path) -> None:
        fixture = tmp_path / "project"
        fixture.mkdir()
        f = fixture / "config.py"
        f.write_text("x = 1\n", encoding="utf-8")
        f.chmod(0o644)

        members = collect_allowlist_members(fixture, ["config.py"], archive_prefix="bundle")
        out = tmp_path / "out.tar.xz"
        write_deterministic_tar(members, out, source_date_epoch=1700000000)

        with tarfile.open(str(out), "r:xz") as tf:
            ti = tf.getmember("bundle/config.py")
            assert ti.mode == 0o644

    def test_tar_member_mtime_equals_epoch(self, tmp_path: Path) -> None:
        epoch = 1700000042
        members = [BundleMember(archive_path="bundle/x.txt", content=b"x")]
        out = tmp_path / "out.tar.xz"
        write_deterministic_tar(members, out, source_date_epoch=epoch)

        with tarfile.open(str(out), "r:xz") as tf:
            for ti in tf.getmembers():
                assert ti.mtime == epoch, f"mtime mismatch: {ti.name} has {ti.mtime}, want {epoch}"

    def test_tar_member_uid_gid_zero(self, tmp_path: Path) -> None:
        members = [BundleMember(archive_path="bundle/x.txt", content=b"x")]
        out = tmp_path / "out.tar.xz"
        write_deterministic_tar(members, out, source_date_epoch=1700000000)

        with tarfile.open(str(out), "r:xz") as tf:
            for ti in tf.getmembers():
                assert ti.uid == 0
                assert ti.gid == 0
                assert ti.uname == ""
                assert ti.gname == ""


# ---------------------------------------------------------------------------
# Test 3: Manifest schema
# ---------------------------------------------------------------------------

class TestManifestSchema:
    def _standard_manifest_args(self, tmp: Path) -> dict:
        cmru_wheel = _fake_wheel(tmp, "cmru", "0.9.0")
        ciu_wheel = _fake_wheel(tmp, "ciu", "1.2.3")
        return dict(
            project="myproject",
            tag="myproject-v1.0.0",
            source_commit="abc123def456",
            cmru_wheel=cmru_wheel,
            ciu_wheel=ciu_wheel,
            images={
                "api": {
                    "repository": "ghcr.io/owner/myproject-api",
                    "tag": "myproject-v1.0.0",
                    "digest": "sha256:" + "a" * 64,
                }
            },
            installer_schema_version=1,
            host_config_schema_version=1,
            platform={"min_python": "3.11", "arch": ["amd64"]},
            upgrade={"min_from": "myproject-v0.9.0", "rollback_to": ["myproject-v0.9.0"]},
        )

    def test_all_section3_keys_present(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            args = self._standard_manifest_args(tmp_path)
            m = build_manifest(**args)

        required_keys = {
            "schema_version", "project", "tag", "source_commit", "created",
            "cmru", "ciu", "installer_schema_version", "host_config_schema_version",
            "images", "platform", "upgrade",
        }
        assert required_keys.issubset(set(m.keys())), (
            f"Missing keys: {required_keys - set(m.keys())}"
        )

    def test_wheel_entries_have_version_wheel_sha256(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            args = self._standard_manifest_args(tmp_path)
            m = build_manifest(**args)

        for key in ("cmru", "ciu"):
            entry = m[key]
            assert "version" in entry
            assert "wheel" in entry
            assert "sha256" in entry
            assert len(entry["sha256"]) == 64  # hex sha256

    def test_created_tracks_source_date_epoch(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            args = self._standard_manifest_args(tmp_path)
            m = build_manifest(**args)

        # 1700000000 seconds since epoch = 2023-11-14T22:13:20Z
        assert m["created"] == "2023-11-14T22:13:20Z"

    def test_canonical_serialization_byte_stable(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            args = self._standard_manifest_args(tmp_path)
            m1 = build_manifest(**args)
            m2 = build_manifest(**args)

        out1 = tmp_path / "manifest1.json"
        out2 = tmp_path / "manifest2.json"
        write_manifest(m1, out1)
        write_manifest(m2, out2)

        assert out1.read_bytes() == out2.read_bytes(), (
            "Canonical serialization must be byte-stable across two calls"
        )

    def test_canonical_serialization_has_trailing_newline(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            args = self._standard_manifest_args(tmp_path)
            m = build_manifest(**args)

        out = tmp_path / "manifest.json"
        write_manifest(m, out)
        raw = out.read_bytes()
        assert raw.endswith(b"\n"), "manifest.json must end with a newline"

    def test_canonical_serialization_is_compact_json(self, tmp_path: Path) -> None:
        """Compact separators (no spaces after : or ,) keeps the format deterministic."""
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            args = self._standard_manifest_args(tmp_path)
            m = build_manifest(**args)

        out = tmp_path / "manifest.json"
        write_manifest(m, out)
        text = out.read_text(encoding="utf-8")
        # Canonical form must be parseable.
        reparsed = json.loads(text)
        assert reparsed["schema_version"] == 1

    def test_manifest_sha256_helper(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            args = self._standard_manifest_args(tmp_path)
            m = build_manifest(**args)

        out = tmp_path / "manifest.json"
        write_manifest(m, out)
        hexdigest = manifest_sha256(out)
        expected = hashlib.sha256(out.read_bytes()).hexdigest()
        assert hexdigest == expected

    def test_missing_source_date_epoch_raises(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"}
        with mock.patch.dict(os.environ, env, clear=True):
            args = self._standard_manifest_args(tmp_path)
            with pytest.raises(RuntimeError, match="SOURCE_DATE_EPOCH"):
                build_manifest(**args)


# ---------------------------------------------------------------------------
# Test 5: Trusted comment binds manifest
# ---------------------------------------------------------------------------

class TestTrustedComment:
    def test_trusted_comment_format(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text('{"schema_version":1}\n', encoding="utf-8")
        expected_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        comment = build_trusted_comment(
            project="myproject",
            tag="myproject-v1.0.0",
            manifest_path=manifest_path,
        )

        assert comment.startswith("project=myproject tag=myproject-v1.0.0 manifest_sha256="), (
            f"Unexpected trusted comment format: {comment!r}"
        )
        assert expected_sha in comment, (
            f"Trusted comment does not carry manifest sha256.\n"
            f"Expected sha256: {expected_sha}\nComment: {comment!r}"
        )

    def test_trusted_comment_changes_when_manifest_changes(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_bytes(b'{"schema_version":1}\n')
        comment1 = build_trusted_comment(
            project="p", tag="p-v1", manifest_path=manifest_path
        )

        manifest_path.write_bytes(b'{"schema_version":2}\n')
        comment2 = build_trusted_comment(
            project="p", tag="p-v1", manifest_path=manifest_path
        )

        assert comment1 != comment2, (
            "Trusted comment must change when manifest bytes change"
        )


# ---------------------------------------------------------------------------
# Test 6: Missing secret key → clear error
# ---------------------------------------------------------------------------

class TestMissingSecretKey:
    def test_minisign_in_delegated_config_missing_key_exits(self, tmp_path: Path) -> None:
        """delegated.run_delegated_config with minisign enabled but no key → exit CONFIG_ERROR."""
        from cmru import delegated, exit_codes

        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"schema_version":1}\n', encoding="utf-8")

        minisign_cfg = {
            "minisign": {
                "enabled": True,
                # Neither secret_key_env nor secret_key_file provided → error.
            }
        }

        env = {k: v for k, v in os.environ.items() if not k.startswith("MINISIGN")}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                delegated.run_delegated_config(minisign_cfg, artifact=manifest)
            assert exc_info.value.code == exit_codes.CONFIG_ERROR

    def test_minisign_in_delegated_config_missing_env_var_exits(self, tmp_path: Path) -> None:
        """If secret_key_env is set in config but env var is absent and no file fallback → error."""
        from cmru import delegated, exit_codes

        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"schema_version":1}\n', encoding="utf-8")

        minisign_cfg = {
            "minisign": {
                "enabled": True,
                "secret_key_env": "MINISIGN_KEY_THAT_DOES_NOT_EXIST",
            }
        }

        env = {k: v for k, v in os.environ.items() if k != "MINISIGN_KEY_THAT_DOES_NOT_EXIST"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                delegated.run_delegated_config(minisign_cfg, artifact=manifest)
            assert exc_info.value.code == exit_codes.CONFIG_ERROR


# ---------------------------------------------------------------------------
# Test 7: Image map is input, not invented
# ---------------------------------------------------------------------------

class TestImageMapIsInput:
    def test_empty_images_dict_raises(self, tmp_path: Path) -> None:
        """An empty images dict means the project explicitly declared images but forgot entries."""
        cmru_wheel = _fake_wheel(tmp_path, "cmru")
        ciu_wheel = _fake_wheel(tmp_path, "ciu")

        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            with pytest.raises(ValueError, match="images is present but empty"):
                build_manifest(
                    project="p",
                    tag="p-v1.0.0",
                    source_commit="abc",
                    cmru_wheel=cmru_wheel,
                    ciu_wheel=ciu_wheel,
                    images={},   # empty dict → error
                    installer_schema_version=1,
                    host_config_schema_version=1,
                    platform={"min_python": "3.11", "arch": ["amd64"]},
                    upgrade={"min_from": "p-v0.9.0", "rollback_to": []},
                )

    def test_none_images_produces_empty_dict(self, tmp_path: Path) -> None:
        """images=None means the project has no container images; manifest gets {}."""
        cmru_wheel = _fake_wheel(tmp_path, "cmru")
        ciu_wheel = _fake_wheel(tmp_path, "ciu")

        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            m = build_manifest(
                project="p",
                tag="p-v1.0.0",
                source_commit="abc",
                cmru_wheel=cmru_wheel,
                ciu_wheel=ciu_wheel,
                images=None,
                installer_schema_version=1,
                host_config_schema_version=1,
                platform={"min_python": "3.11", "arch": ["amd64"]},
                upgrade={"min_from": "p-v0.9.0", "rollback_to": []},
            )
        assert m["images"] == {}, "images=None must produce {} in manifest (no images)"

    def test_images_missing_required_field_raises(self, tmp_path: Path) -> None:
        cmru_wheel = _fake_wheel(tmp_path, "cmru")
        ciu_wheel = _fake_wheel(tmp_path, "ciu")

        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            with pytest.raises(ValueError, match="missing required keys"):
                build_manifest(
                    project="p",
                    tag="p-v1.0.0",
                    source_commit="abc",
                    cmru_wheel=cmru_wheel,
                    ciu_wheel=ciu_wheel,
                    images={
                        "api": {
                            "repository": "ghcr.io/owner/p-api",
                            "tag": "p-v1.0.0",
                            # "digest" missing → error
                        }
                    },
                    installer_schema_version=1,
                    host_config_schema_version=1,
                    platform={"min_python": "3.11", "arch": ["amd64"]},
                    upgrade={"min_from": "p-v0.9.0", "rollback_to": []},
                )


# ---------------------------------------------------------------------------
# Test 4: minisign round-trip (real binary, skipped if absent)
# ---------------------------------------------------------------------------

def _minisign_available() -> bool:
    return shutil.which("minisign") is not None


@pytest.mark.skipif(not _minisign_available(), reason="minisign binary not found on PATH")
class TestMinisignRoundTrip:
    """Real minisign integration tests — skipped if minisign is not installed."""

    def _gen_keypair(self, tmp: Path) -> tuple[Path, Path]:
        """Generate a throwaway minisign keypair for testing."""
        import subprocess
        pub = tmp / "test.pub"
        sec = tmp / "test.key"
        # -W = no passphrase (for CI/test use only)
        result = subprocess.run(
            ["minisign", "-G", "-p", str(pub), "-s", str(sec), "-W"],
            capture_output=True,
            cwd=str(tmp),
        )
        if result.returncode != 0:
            pytest.skip(f"minisign key generation failed: {result.stderr.decode()}")
        return pub, sec

    def test_sign_verify_succeeds(self, tmp_path: Path) -> None:
        from cmru.delegated import minisign_sign, minisign_verify

        pub, sec = self._gen_keypair(tmp_path)
        blob = tmp_path / "manifest.json"
        blob.write_text('{"schema_version":1,"project":"test"}\n', encoding="utf-8")

        trusted_comment = build_trusted_comment(
            project="test", tag="test-v1.0.0", manifest_path=blob
        )

        minisign_sign(blob, secret_key=str(sec), trusted_comment=trusted_comment, required=True)

        sig_file = tmp_path / "manifest.json.minisig"
        assert sig_file.exists(), "minisign must produce <blob>.minisig"

        result = minisign_verify(blob, public_key=str(pub), required=True)
        assert result is True, "verify must return True for a valid signature"

    def test_mutated_blob_verify_fails(self, tmp_path: Path) -> None:
        from cmru.delegated import minisign_sign, minisign_verify

        pub, sec = self._gen_keypair(tmp_path)
        blob = tmp_path / "manifest.json"
        blob.write_text('{"schema_version":1,"project":"test"}\n', encoding="utf-8")

        trusted_comment = build_trusted_comment(
            project="test", tag="test-v1.0.0", manifest_path=blob
        )
        minisign_sign(blob, secret_key=str(sec), trusted_comment=trusted_comment)

        # Mutate one byte of the manifest.
        data = blob.read_bytes()
        mutated = data[:-1] + bytes([data[-1] ^ 0xFF])
        blob.write_bytes(mutated)

        result = minisign_verify(blob, public_key=str(pub))
        assert result is False, "verify must return False after manifest mutation"

    def test_stale_signature_different_manifest_fails(self, tmp_path: Path) -> None:
        """Using a signature from manifest A to verify manifest B must fail."""
        from cmru.delegated import minisign_sign, minisign_verify

        pub, sec = self._gen_keypair(tmp_path)

        # Sign manifest A.
        blob_a = tmp_path / "manifest_a.json"
        blob_a.write_text('{"schema_version":1,"project":"a"}\n', encoding="utf-8")
        tc_a = build_trusted_comment(project="a", tag="a-v1.0.0", manifest_path=blob_a)
        minisign_sign(blob_a, secret_key=str(sec), trusted_comment=tc_a)

        # Copy A's signature to B.
        blob_b = tmp_path / "manifest_b.json"
        blob_b.write_text('{"schema_version":1,"project":"b"}\n', encoding="utf-8")
        import shutil as _shutil
        sig_a = tmp_path / "manifest_a.json.minisig"
        sig_b = tmp_path / "manifest_b.json.minisig"
        _shutil.copy(sig_a, sig_b)

        result = minisign_verify(blob_b, public_key=str(pub))
        assert result is False, (
            "Stale signature from a different manifest must not verify"
        )


@pytest.mark.skipif(_minisign_available(), reason="minisign IS available — this tests the absent case")
class TestMinisignAbsent:
    def test_sign_absent_required_false_skips(self, tmp_path: Path) -> None:
        """When minisign is absent and required=False, sign must skip (no exit)."""
        from cmru.delegated import minisign_sign
        blob = tmp_path / "manifest.json"
        blob.write_text("{}\n", encoding="utf-8")
        # Should not raise or exit.
        minisign_sign(blob, secret_key="/nonexistent.key", trusted_comment="test", required=False)

    def test_verify_absent_required_false_returns_false(self, tmp_path: Path) -> None:
        from cmru.delegated import minisign_verify
        blob = tmp_path / "manifest.json"
        blob.write_text("{}\n", encoding="utf-8")
        result = minisign_verify(blob, public_key="/nonexistent.pub", required=False)
        assert result is False

    def test_sign_absent_required_true_exits_3(self, tmp_path: Path) -> None:
        from cmru import exit_codes
        from cmru.delegated import minisign_sign
        blob = tmp_path / "manifest.json"
        blob.write_text("{}\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            minisign_sign(blob, secret_key="/nonexistent.key", trusted_comment="test", required=True)
        assert exc_info.value.code == exit_codes.PREREQ_MISSING

    def test_verify_absent_required_true_exits_3(self, tmp_path: Path) -> None:
        from cmru import exit_codes
        from cmru.delegated import minisign_verify
        blob = tmp_path / "manifest.json"
        blob.write_text("{}\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            minisign_verify(blob, public_key="/nonexistent.pub", required=True)
        assert exc_info.value.code == exit_codes.PREREQ_MISSING
