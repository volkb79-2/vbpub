"""Focused regression tests for install_ai_cli_tools.py.

Uses mocks and temp paths; never reaches the network or installs packages.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from install_ai_cli_tools import (
    DEFAULT_DOWNLOADS_DIR,
    InstallerContext,
    InstallerError,
    archive_missing_binaries,
    copy_binary,
    env_value,
    install_binaries_from_archive,
    install_codex,
    install_opencode,
    is_enabled,
    parse_tool_entries,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _fake_ctx(tmp_path: Path) -> InstallerContext:
    return InstallerContext(
        tools_file=tmp_path / "ai-cli-tools.list",
        downloads_dir=tmp_path / "downloads",
        venv_python=Path(sys.executable),
    )


def _write_tools_file(ctx: InstallerContext, *tools: str) -> None:
    ctx.tools_file.write_text(
        "\n".join(f"{t}|root" for t in tools) + "\n",
        encoding="utf-8",
    )


def _make_tar_with_binaries(path: Path, *names: str) -> None:
    """Create a minimal tar.gz with one or more executable files inside ``bin/``."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name in names:
            exe = tmp / "bin" / name
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("#!/bin/sh\necho mock\n")
            exe.chmod(0o755)
        with tarfile.open(str(path), "w:gz") as tf:
            tf.add(str(tmp), arcname="")
    assert path.is_file(), f"Failed to create mock archive: {path}"


def _make_checksums(path: Path, archive_name: str, sha256_digest: str) -> None:
    path.write_text(f"{sha256_digest}  {archive_name}\n", encoding="utf-8")


# ── archive_missing_binaries ─────────────────────────────────────────────


class TestArchiveMissingBinaries:
    def test_both_present(self, tmp_path: Path) -> None:
        archive = tmp_path / "pkg.tar.gz"
        _make_tar_with_binaries(archive, "codex", "codex-code-mode-host")
        assert archive_missing_binaries(archive, "codex", "codex-code-mode-host") == []

    def test_one_missing(self, tmp_path: Path) -> None:
        archive = tmp_path / "pkg.tar.gz"
        _make_tar_with_binaries(archive, "codex")
        missing = archive_missing_binaries(archive, "codex", "codex-code-mode-host")
        assert missing == ["codex-code-mode-host"]

    def test_all_missing(self, tmp_path: Path) -> None:
        archive = tmp_path / "pkg.tar.gz"
        _make_tar_with_binaries(archive)  # empty archive
        missing = archive_missing_binaries(archive, "codex", "codex-code-mode-host")
        assert sorted(missing) == ["codex", "codex-code-mode-host"]

    def test_non_tar_archive_returns_empty(self, tmp_path: Path) -> None:
        archive = tmp_path / "not-a-tar.gz"
        archive.write_text("garbage", encoding="utf-8")
        assert archive_missing_binaries(archive, "codex") == []


# ── install_binaries_from_archive ──────────────────────────────────────


class TestInstallBinariesFromArchive:
    def test_installs_multiple_binaries(self, tmp_path: Path) -> None:
        archive = tmp_path / "pkg.tar.gz"
        _make_tar_with_binaries(archive, "codex", "codex-code-mode-host")
        dest = tmp_path / "dest"
        install_binaries_from_archive(
            archive,
            ("codex", dest / "usr/local/bin/codex"),
            ("codex-code-mode-host", dest / "usr/local/bin/codex-code-mode-host"),
        )
        assert (dest / "usr/local/bin/codex").is_file()
        assert (dest / "usr/local/bin/codex-code-mode-host").is_file()
        assert oct((dest / "usr/local/bin/codex").stat().st_mode)[-3:] == "755"

    def test_missing_binary_raises(self, tmp_path: Path) -> None:
        archive = tmp_path / "pkg.tar.gz"
        _make_tar_with_binaries(archive, "codex")
        dest = tmp_path / "dest"
        with pytest.raises(InstallerError, match="Failed to locate codex-code-mode-host"):
            install_binaries_from_archive(
                archive,
                ("codex", dest / "codex"),
                ("codex-code-mode-host", dest / "codex-code-mode-host"),
            )


# ── install_codex ───────────────────────────────────────────────────────


class TestInstallCodex:
    def test_skipped_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _fake_ctx(tmp_path)
        monkeypatch.setenv("INSTALL_CODEX", "false")
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        # Should not raise even though files are absent.
        install_codex(ctx)

    def test_missing_archive_raises(self, tmp_path: Path) -> None:
        ctx = _fake_ctx(tmp_path)
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        with pytest.raises(InstallerError, match="Missing staged Codex archive"):
            install_codex(ctx)
        monkeypatch.undo()

    def test_checksum_mismatch_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _fake_ctx(tmp_path)
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        archive = ctx.downloads_dir / "codex-0.144.0.tar.gz"
        sums = ctx.downloads_dir / "codex-0.144.0-SHA256SUMS"
        ctx.downloads_dir.mkdir(parents=True, exist_ok=True)
        archive.write_text("garbage", encoding="utf-8")
        _make_checksums(sums, "codex-package-x86_64-unknown-linux-musl.tar.gz", "00" * 32)
        with pytest.raises(InstallerError, match="Codex checksum mismatch"):
            install_codex(ctx)
        monkeypatch.undo()

    def test_missing_companion_host_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Archive has codex but not codex-code-mode-host → should fail."""
        ctx = _fake_ctx(tmp_path)
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        ctx.downloads_dir.mkdir(parents=True, exist_ok=True)
        archive = ctx.downloads_dir / "codex-0.144.0.tar.gz"
        _make_tar_with_binaries(archive, "codex")  # missing codex-code-mode-host

        sums = ctx.downloads_dir / "codex-0.144.0-SHA256SUMS"
        import hashlib
        actual_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        _make_checksums(sums, "codex-package-x86_64-unknown-linux-musl.tar.gz", actual_sha)

        with pytest.raises(InstallerError, match="missing required.*codex-code-mode-host"):
            install_codex(ctx)
        monkeypatch.undo()

    def test_successful_two_binary_install(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both codex and codex-code-mode-host are extracted and placed in /usr/local/bin."""
        ctx = _fake_ctx(tmp_path)
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        ctx.downloads_dir.mkdir(parents=True, exist_ok=True)
        archive = ctx.downloads_dir / "codex-0.144.0.tar.gz"
        _make_tar_with_binaries(archive, "codex", "codex-code-mode-host")

        sums = ctx.downloads_dir / "codex-0.144.0-SHA256SUMS"
        import hashlib
        actual_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        _make_checksums(sums, "codex-package-x86_64-unknown-linux-musl.tar.gz", actual_sha)

        # Redirect binary installation to a temp location.
        dest_root = tmp_path / "installed"

        def tracking_copy(source: Path, destination: Path) -> None:
            rel_dest = destination.relative_to(Path("/")) if destination.is_absolute() else destination
            real_dest = dest_root / str(rel_dest).lstrip("/")
            real_dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(source, real_dest)
            real_dest.chmod(0o755)

        import install_ai_cli_tools as mod
        original_copy = mod.copy_binary
        try:
            mod.copy_binary = tracking_copy
            install_codex(ctx)
        finally:
            mod.copy_binary = original_copy

        codex_dest = dest_root / "usr/local/bin/codex"
        host_dest = dest_root / "usr/local/bin/codex-code-mode-host"
        assert codex_dest.is_file(), f"codex not found at {codex_dest}"
        assert host_dest.is_file(), f"codex-code-mode-host not found at {host_dest}"
        assert oct(codex_dest.stat().st_mode)[-3:] == "755"
        assert oct(host_dest.stat().st_mode)[-3:] == "755"


# ── install_opencode ────────────────────────────────────────────────────


class TestInstallOpencode:
    def test_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSTALL_OPENCODE", "false")
        install_opencode()  # should not raise

    def test_command_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the npm command that would be issued."""
        monkeypatch.setenv("OPENCODE_VER", "1.2.3")
        monkeypatch.setenv("INSTALL_OPENCODE", "true")

        recorded: list[list[str]] = []

        def fake_run(argv: list[str]) -> None:
            recorded.append(argv)
            # Simulate success
            return

        import install_ai_cli_tools as mod
        original_run = mod.run_command
        try:
            mod.run_command = fake_run
            install_opencode()
        finally:
            mod.run_command = original_run

        assert len(recorded) == 1
        assert recorded[0] == ["npm", "install", "-g", "opencode-ai@1.2.3"]

    def test_version_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENCODE_VERSION is used when OPENCODE_VER is not set."""
        monkeypatch.delenv("OPENCODE_VER", raising=False)
        monkeypatch.setenv("OPENCODE_VERSION", "2.0.0")
        recorded: list[list[str]] = []

        def fake_run(argv: list[str]) -> None:
            recorded.append(argv)

        import install_ai_cli_tools as mod
        original_run = mod.run_command
        try:
            mod.run_command = fake_run
            install_opencode()
        finally:
            mod.run_command = original_run

        assert len(recorded) == 1
        assert recorded[0] == ["npm", "install", "-g", "opencode-ai@2.0.0"]


# ── parse_tool_entries ──────────────────────────────────────────────────


class TestParseToolEntries:
    def test_opencode_entry(self, tmp_path: Path) -> None:
        f = tmp_path / "list"
        f.write_text("opencode|root\n", encoding="utf-8")
        entries = parse_tool_entries(f)
        assert ("opencode", "root") in entries

    def test_comment_and_blank_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "list"
        f.write_text("# comment\n\ncodex|root\n  \nopencode|root\n", encoding="utf-8")
        entries = parse_tool_entries(f)
        assert ("codex", "root") in entries
        assert ("opencode", "root") in entries


# ── env / toggle helpers ────────────────────────────────────────────────


class TestIsEnabled:
    def test_default_true(self) -> None:
        assert is_enabled("UNSET_VAR_THAT_SHOULD_DEFAULT_TRUE", True) is True

    def test_explicit_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSTALL_OPENCODE", "0")
        assert is_enabled("INSTALL_OPENCODE", True) is False


class TestEnvValue:
    def test_first_name_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENCODE_VER", "1.2.3")
        monkeypatch.setenv("OPENCODE_VERSION", "4.5.6")
        assert env_value("OPENCODE_VER", "OPENCODE_VERSION") == "1.2.3"

    def test_second_name_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENCODE_VER", raising=False)
        monkeypatch.setenv("OPENCODE_VERSION", "2.0.0")
        assert env_value("OPENCODE_VER", "OPENCODE_VERSION") == "2.0.0"
