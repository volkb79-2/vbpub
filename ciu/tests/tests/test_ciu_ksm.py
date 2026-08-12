"""S15.17 — `ciu ksm build`: cache keying, verification, and refusal paths.

The verification tests matter more than the happy path here. A shim that builds
but carries a libc dependency is not a degraded shim — it is a container that
will not start under the other libc (glibc's ld.so exits 127 on a musl-linked
object). So "refuses to cache a bad artifact" is the behavioural contract.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import ksm


def _elf64(dt_needed: int = 0) -> bytes:
    """A minimal 64-bit little-endian ELF with one PT_DYNAMIC segment carrying
    *dt_needed* DT_NEEDED entries. Built by hand so the tests exercise the real
    parser rather than a stub of it."""
    PH_OFF, PH_ENTSIZE = 64, 56
    dyn_off = PH_OFF + PH_ENTSIZE
    entries = b"".join(
        (1).to_bytes(8, "little") + (0).to_bytes(8, "little")
        for _ in range(dt_needed)
    ) + (0).to_bytes(8, "little") * 2  # DT_NULL terminator

    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4] = 2          # 64-bit
    hdr[5] = 1          # little-endian
    hdr[32:40] = PH_OFF.to_bytes(8, "little")     # e_phoff
    hdr[54:56] = PH_ENTSIZE.to_bytes(2, "little")  # e_phentsize
    hdr[56:58] = (1).to_bytes(2, "little")         # e_phnum

    ph = bytearray(PH_ENTSIZE)
    ph[0:4] = (2).to_bytes(4, "little")            # PT_DYNAMIC
    ph[8:16] = dyn_off.to_bytes(8, "little")       # p_offset
    ph[32:40] = len(entries).to_bytes(8, "little")  # p_filesz
    return bytes(hdr) + bytes(ph) + entries


class TestCacheKey:
    def test_cache_path_is_keyed_by_machine_and_source_digest(self, tmp_path):
        """Arch keying stops an x86-64 object being reused on aarch64; the source
        digest makes a CIU upgrade that changes the shim self-invalidating."""
        src = tmp_path / "shim.c"
        src.write_text("int a;", encoding="utf-8")
        first = ksm.cache_path(tmp_path, src)

        assert platform.machine() in first.name
        assert first.parent == tmp_path / ".ciu" / "ksm"

        src.write_text("int b;", encoding="utf-8")
        assert ksm.cache_path(tmp_path, src) != first

    def test_cache_lives_under_the_repo_root(self, tmp_path):
        """Load-bearing: the path becomes a Docker bind source, so it must sit
        where to_physical_path (S1.3/S1.4) can translate it. A ~/.cache location
        would pass through untranslated and Docker would create it empty on the
        host — CIU-14 from a different direction."""
        assert ksm.cache_path(tmp_path).is_relative_to(tmp_path)

    def test_wrapper_cache_uses_its_own_source_digest_and_executable_name(self, tmp_path):
        src = tmp_path / "wrapper.c"
        src.write_text("int main(void) {}", encoding="utf-8")

        target = ksm.wrapper_cache_path(tmp_path, src)

        assert target.parent == tmp_path / ".ciu" / "ksm"
        assert target.name.startswith("ksm-exec-")
        src.write_text("int main(void) { return 1; }", encoding="utf-8")
        assert ksm.wrapper_cache_path(tmp_path, src) != target


class TestVerification:
    def test_dependency_free_object_is_accepted(self, tmp_path):
        so = tmp_path / "ok.so"
        so.write_bytes(_elf64(dt_needed=0))
        ksm._verify(so)  # must not raise

    def test_libc_linked_object_is_refused(self, tmp_path):
        so = tmp_path / "bad.so"
        so.write_bytes(_elf64(dt_needed=1))
        with pytest.raises(ksm.KsmBuildError, match=r"DT_NEEDED"):
            ksm._verify(so)

    def test_empty_file_is_refused(self, tmp_path):
        so = tmp_path / "empty.so"
        so.write_bytes(b"")
        with pytest.raises(ksm.KsmBuildError, match=r"EMPTY"):
            ksm._verify(so)

    def test_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ksm.KsmBuildError, match=r"no file"):
            ksm._verify(tmp_path / "absent.so")

    def test_unverifiable_object_is_refused_not_assumed_good(self, tmp_path):
        """An artifact we cannot parse must NOT be treated as passing. Conflating
        "verified good" with "could not check" is how an unloadable shim reaches
        production."""
        so = tmp_path / "notelf.so"
        so.write_bytes(b"this is not an ELF object at all, but it is non-empty")
        with pytest.raises(ksm.KsmBuildError, match=r"cannot verify"):
            ksm._verify(so)

    def test_dt_needed_parser_refuses_unreadable_and_32_bit_inputs(self, tmp_path):
        assert ksm._dt_needed_count(tmp_path / "absent.so") is None
        object_32 = bytearray(_elf64())
        object_32[4] = 1
        path = tmp_path / "32-bit.so"
        path.write_bytes(object_32)
        assert ksm._dt_needed_count(path) is None

    def test_dt_needed_parser_refuses_truncated_program_or_dynamic_entries(self, tmp_path):
        bad_header = bytearray(_elf64())
        bad_header[32:40] = (10_000).to_bytes(8, "little")
        header_path = tmp_path / "bad-header.so"
        header_path.write_bytes(bad_header)
        assert ksm._dt_needed_count(header_path) is None

        bad_dynamic = bytearray(_elf64(dt_needed=1))
        dynamic_offset = 64 + 56
        bad_dynamic[dynamic_offset + 16:dynamic_offset + 24] = (1).to_bytes(8, "little")
        bad_dynamic[64 + 32:64 + 40] = (48).to_bytes(8, "little")
        dynamic_path = tmp_path / "bad-dynamic.so"
        dynamic_path.write_bytes(bad_dynamic)
        assert ksm._dt_needed_count(dynamic_path) is None

    def test_dt_needed_parser_counts_a_segment_without_a_null_terminator(self, tmp_path):
        unterminated = bytearray(_elf64(dt_needed=1))
        dynamic_offset = 64 + 56
        unterminated[dynamic_offset + 16:dynamic_offset + 24] = (1).to_bytes(8, "little")
        path = tmp_path / "unterminated.so"
        path.write_bytes(unterminated)
        assert ksm._dt_needed_count(path) == 2

    def test_dt_needed_parser_accepts_an_elf_with_no_dynamic_segment(self, tmp_path):
        no_dynamic = bytearray(_elf64())
        no_dynamic[64:68] = (1).to_bytes(4, "little")  # PT_LOAD, not PT_DYNAMIC
        path = tmp_path / "static.so"
        path.write_bytes(no_dynamic)
        assert ksm._dt_needed_count(path) == 0


class TestWrapperVerification:
    def test_wrapper_verification_rejects_missing_empty_non_elf_and_non_executable(self, tmp_path, monkeypatch):
        missing = tmp_path / "absent"
        with pytest.raises(ksm.KsmBuildError, match="no file"):
            ksm._verify_wrapper(missing)

        empty = tmp_path / "empty"
        empty.write_bytes(b"")
        with pytest.raises(ksm.KsmBuildError, match="EMPTY"):
            ksm._verify_wrapper(empty)

        not_elf = tmp_path / "not-elf"
        not_elf.write_bytes(b"text")
        with pytest.raises(ksm.KsmBuildError, match="not an ELF"):
            ksm._verify_wrapper(not_elf)

        no_exec = tmp_path / "no-exec"
        no_exec.write_bytes(b"\x7fELF")
        monkeypatch.setattr(ksm.os, "access", lambda *_args: False)
        with pytest.raises(ksm.KsmBuildError, match="not executable"):
            ksm._verify_wrapper(no_exec)


class TestBuild:
    def test_shim_source_missing_refuses_before_docker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ksm, "shim_source_path", lambda: tmp_path / "absent.c")

        with pytest.raises(ksm.KsmBuildError, match="source is missing"):
            ksm.build(tmp_path, "/host/repo")

    def test_cached_artifact_is_reused_without_invoking_docker(self, tmp_path, monkeypatch):
        target = ksm.cache_path(tmp_path)
        target.parent.mkdir(parents=True)
        target.write_bytes(_elf64(dt_needed=0))

        def fail(*_a, **_kw):  # pragma: no cover - must never run
            raise AssertionError("docker was invoked despite a valid cache hit")

        monkeypatch.setattr(ksm.subprocess, "run", fail)
        assert ksm.build(tmp_path, "/host/repo") == target

    def test_cached_artifact_is_still_verified(self, tmp_path):
        """A cache hit is not a free pass: a corrupted/wrong cached object must
        be caught rather than handed back because it exists."""
        target = ksm.cache_path(tmp_path)
        target.parent.mkdir(parents=True)
        target.write_bytes(_elf64(dt_needed=2))
        with pytest.raises(ksm.KsmBuildError, match=r"DT_NEEDED"):
            ksm.build(tmp_path, "/host/repo")

    def test_missing_docker_fails_with_an_actionable_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ksm.shutil, "which", lambda _n: None)
        with pytest.raises(ksm.KsmBuildError, match=r"docker is required"):
            ksm.build(tmp_path, "/host/repo")

    def test_build_mounts_the_PHYSICAL_path_not_the_logical_one(self, tmp_path, monkeypatch):
        """The bind source must be what the DOCKER DAEMON sees. Passing the
        container-local path would make docker create a phantom empty dir, so
        the build would write where the caller cannot see and 'succeed' with
        nothing."""
        seen: list[list[str]] = []

        def fake_run(cmd, **_kw):
            seen.append(cmd)
            target = ksm.cache_path(tmp_path)
            target.write_bytes(_elf64(dt_needed=0))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(ksm.subprocess, "run", fake_run)

        ksm.build(tmp_path, "/host/repo")

        mount = seen[0][seen[0].index("-v") + 1]
        assert mount.startswith("/host/repo/")
        assert str(tmp_path) not in mount

    def test_a_bad_build_is_never_left_in_the_cache(self, tmp_path, monkeypatch):
        """After a refusal the artifact must be GONE, so a later run cannot find
        it, skip the build, and trust it."""
        def fake_run(cmd, **_kw):
            ksm.cache_path(tmp_path).write_bytes(_elf64(dt_needed=1))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(ksm.subprocess, "run", fake_run)

        with pytest.raises(ksm.KsmBuildError, match=r"DT_NEEDED"):
            ksm.build(tmp_path, "/host/repo")
        assert not ksm.cache_path(tmp_path).exists()

    def test_failed_docker_build_surfaces_its_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(
            ksm.subprocess, "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, "", "no such image"),
        )
        with pytest.raises(ksm.KsmBuildError, match=r"no such image"):
            ksm.build(tmp_path, "/host/repo")

    def test_build_timeout_is_reported_not_swallowed(self, tmp_path, monkeypatch):
        def timeout(cmd, **_kw):
            raise subprocess.TimeoutExpired(cmd, ksm.BUILD_TIMEOUT_SECONDS)

        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(ksm.subprocess, "run", timeout)
        with pytest.raises(ksm.KsmBuildError, match=r"timed out"):
            ksm.build(tmp_path, "/host/repo")

    def test_build_oserror_is_reported_not_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(ksm.subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no docker")))

        with pytest.raises(ksm.KsmBuildError, match=r"could not run docker"):
            ksm.build(tmp_path, "/host/repo")


class TestWrapperBuild:
    def test_wrapper_source_missing_refuses_before_docker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ksm, "wrapper_source_path", lambda: tmp_path / "absent.c")

        with pytest.raises(ksm.KsmBuildError, match="source is missing"):
            ksm.build_wrapper(tmp_path, "/host/repo")

    def test_cached_wrapper_is_verified_and_reused(self, tmp_path, monkeypatch):
        target = ksm.wrapper_cache_path(tmp_path)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\x7fELF")
        target.chmod(0o755)
        monkeypatch.setattr(ksm.subprocess, "run", lambda *_a, **_kw: pytest.fail("docker invoked"))

        assert ksm.build_wrapper(tmp_path, "/host/repo") == target

    def test_wrapper_missing_docker_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ksm.shutil, "which", lambda _n: None)

        with pytest.raises(ksm.KsmBuildError, match="docker is required"):
            ksm.build_wrapper(tmp_path, "/host/repo")

    def test_wrapper_build_mounts_physical_path_and_verifies_output(self, tmp_path, monkeypatch):
        seen: list[list[str]] = []

        def fake_run(cmd, **_kw):
            seen.append(cmd)
            target = ksm.wrapper_cache_path(tmp_path)
            target.write_bytes(b"\x7fELF")
            target.chmod(0o755)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(ksm.subprocess, "run", fake_run)

        assert ksm.build_wrapper(tmp_path, "/host/repo") == ksm.wrapper_cache_path(tmp_path)
        mount = seen[0][seen[0].index("-v") + 1]
        assert mount.startswith("/host/repo/")

    def test_bad_wrapper_build_is_removed_from_cache(self, tmp_path, monkeypatch):
        def fake_run(cmd, **_kw):
            ksm.wrapper_cache_path(tmp_path).write_bytes(b"not an elf")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(ksm.subprocess, "run", fake_run)

        with pytest.raises(ksm.KsmBuildError, match="not an ELF"):
            ksm.build_wrapper(tmp_path, "/host/repo")
        assert not ksm.wrapper_cache_path(tmp_path).exists()

    @pytest.mark.parametrize(
        ("result", "message"),
        [
            (subprocess.CompletedProcess(["docker"], 1, "", "broken compiler"), "broken compiler"),
            (subprocess.TimeoutExpired(["docker"], ksm.BUILD_TIMEOUT_SECONDS), "timed out"),
            (OSError("docker unavailable"), "could not run docker"),
        ],
    )
    def test_wrapper_build_surfaces_all_execution_failures(self, tmp_path, monkeypatch, result, message):
        def fake_run(*_args, **_kwargs):
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(ksm.shutil, "which", lambda _n: "/usr/bin/docker")
        monkeypatch.setattr(ksm.subprocess, "run", fake_run)

        with pytest.raises(ksm.KsmBuildError, match=message):
            ksm.build_wrapper(tmp_path, "/host/repo")


class TestImageEntrypointFailures:
    @pytest.mark.parametrize("failure", [OSError("docker absent"), subprocess.TimeoutExpired(["docker"], 30)])
    def test_image_entrypoint_returns_none_when_inspect_cannot_run(self, monkeypatch, failure):
        monkeypatch.setattr(
            ksm.subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(failure)
        )
        assert ksm.image_entrypoint("example:test") is None

    def test_image_entrypoint_returns_none_for_malformed_json(self, monkeypatch):
        monkeypatch.setattr(
            ksm.subprocess, "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, "not-json", ""),
        )
        assert ksm.image_entrypoint("example:test") is None


def test_dt_needed_parser_discriminates_on_a_REAL_dynamic_binary():
    """The other parser tests run on hand-built fixtures, which only prove the
    parser agrees with the test's own idea of ELF. This one runs it against a
    real, ordinary dynamically-linked executable: it MUST report a non-zero
    DT_NEEDED count. Without it, a parser that always returned 0 would pass
    every other test in this file while accepting a libc-linked shim.
    """
    real = Path(sys.executable).resolve()
    count = ksm._dt_needed_count(real)
    assert count is not None and count > 0, (
        f"{real} is a normal dynamic executable; a parser reporting "
        f"{count} DT_NEEDED cannot distinguish a libc-linked shim"
    )


def test_shipped_shim_source_is_installed_with_the_package():
    """Package data, not a repo-relative accident: if the wheel omits the .c the
    verb fails at runtime instead of at packaging time."""
    src = ksm.shim_source_path()
    assert src.is_file()
    assert "PR_SET_MEMORY_MERGE" in src.read_text(encoding="utf-8")
