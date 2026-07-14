"""Focused regression tests for install_ai_cli_tools.py.

Uses mocks and temp paths; never reaches the network or installs packages.
"""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

import stage_tool_artifacts as stage
from install_ai_cli_tools import (
    InstallerContext,
    InstallerError,
    env_value,
    install_binaries_from_archive,
    install_codex,
    install_opencode,
    opencode_platform_package,
    main,
    is_enabled,
    parse_tool_entries,
)


# helpers


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


# install_binaries_from_archive


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


# install_codex


class TestInstallCodex:
    def test_skipped_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _fake_ctx(tmp_path)
        monkeypatch.setenv("INSTALL_CODEX", "false")
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        # Should not raise even though files are absent.
        install_codex(ctx)

    def test_uses_official_user_installer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        recorded: list[list[str]] = []
        monkeypatch.setattr(
            "install_ai_cli_tools.run_command", lambda argv: recorded.append(argv)
        )
        install_codex(_fake_ctx(Path("/tmp")))
        assert recorded == [[
            "sh", "-c",
            "curl -fsSL https://chatgpt.com/codex/install.sh | "
            "CODEX_NON_INTERACTIVE=1 CODEX_RELEASE=0.144.0 "
            "CODEX_INSTALL_DIR=/home/vscode/.local/bin sh",
        ]]


# install_opencode


class TestInstallOpencode:
    def test_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSTALL_OPENCODE", "false")
        install_opencode()  # should not raise

    def test_command_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the npm command that would be issued."""
        monkeypatch.setenv("OPENCODE_VER", "1.2.3")
        monkeypatch.setenv("INSTALL_OPENCODE", "true")

        recorded: list[list[str]] = []
        required: list[str] = []
        linked: list[tuple[Path, Path]] = []

        def fake_run(argv: list[str]) -> None:
            recorded.append(argv)
            # Simulate success
            return

        import install_ai_cli_tools as mod
        monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
        monkeypatch.setattr(mod.platform, "libc_ver", lambda: ("glibc", "2.41"))
        monkeypatch.setattr(mod, "require_command", lambda command, _description: required.append(command))
        monkeypatch.setattr(mod, "require_file", lambda *_args: None)
        monkeypatch.setattr(mod, "link_binary", lambda source, destination: linked.append((source, destination)))
        original_run = mod.run_command
        try:
            mod.run_command = fake_run
            install_opencode()
        finally:
            mod.run_command = original_run

        assert len(recorded) == 1
        assert recorded[0] == ["npm", "install", "-g", "opencode-linux-x64-baseline@1.2.3"]
        assert required == ["npm", "opencode"]
        assert linked == [(
            Path("/home/vscode/.local/lib/node_modules/opencode-linux-x64-baseline/bin/opencode"),
            Path("/home/vscode/.local/bin/opencode"),
        )]

    def test_version_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENCODE_VERSION is used when OPENCODE_VER is not set."""
        monkeypatch.delenv("OPENCODE_VER", raising=False)
        monkeypatch.setenv("OPENCODE_VERSION", "2.0.0")
        recorded: list[list[str]] = []

        def fake_run(argv: list[str]) -> None:
            recorded.append(argv)

        import install_ai_cli_tools as mod
        monkeypatch.setattr(mod.platform, "machine", lambda: "aarch64")
        monkeypatch.setattr(mod.platform, "libc_ver", lambda: ("musl", "1.2"))
        monkeypatch.setattr(mod, "require_command", lambda *_args: None)
        monkeypatch.setattr(mod, "require_file", lambda *_args: None)
        monkeypatch.setattr(mod, "link_binary", lambda *_args: None)
        original_run = mod.run_command
        try:
            mod.run_command = fake_run
            install_opencode()
        finally:
            mod.run_command = original_run

        assert len(recorded) == 1
        assert recorded[0] == ["npm", "install", "-g", "opencode-linux-arm64-musl@2.0.0"]

    def test_platform_package_prefers_portable_x64_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import install_ai_cli_tools as mod

        monkeypatch.setattr(mod.platform, "machine", lambda: "amd64")
        monkeypatch.setattr(mod.platform, "libc_ver", lambda: ("glibc", "2.41"))
        assert opencode_platform_package() == "opencode-linux-x64-baseline"

    def test_unsupported_architecture_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import install_ai_cli_tools as mod

        monkeypatch.setattr(mod.platform, "machine", lambda: "riscv64")
        with pytest.raises(InstallerError, match="Unsupported OpenCode architecture"):
            opencode_platform_package()


def test_user_mode_sets_user_owned_npm_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools_file = tmp_path / "tools"
    tools_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("TOOLS_FILE", str(tools_file))
    monkeypatch.delenv("NPM_CONFIG_PREFIX", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")

    assert main(["user"]) == 0
    assert os.environ["NPM_CONFIG_PREFIX"] == "/home/vscode/.local"
    assert os.environ["PATH"].startswith("/home/vscode/.local/bin:")


def test_root_owned_version_file_is_cleaned_after_returning_to_root() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    user_layer = dockerfile.split("USER vscode", 1)[1].split("USER root", 1)[0]
    root_layer = dockerfile.split("USER root", 1)[1]

    assert "rm -f /tmp/ai-tool-versions.env" not in user_layer
    assert "rm -f /tmp/install_ai_cli_tools.py /tmp/ai-cli-tools.list /tmp/ai-tool-versions.env" in root_layer


# parse_tool_entries


class TestParseToolEntries:
    def test_opencode_entry(self, tmp_path: Path) -> None:
        f = tmp_path / "list"
        f.write_text("opencode|root\n", encoding="utf-8")
        entries = parse_tool_entries(f)
        assert ("opencode", "root") in entries

    def test_comment_and_blank_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "list"
        f.write_text("# comment\n\ncodex|user\n  \nopencode|user\n", encoding="utf-8")
        entries = parse_tool_entries(f)
        assert ("codex", "user") in entries
        assert ("opencode", "user") in entries


# env / toggle helpers


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


def test_stage_resolves_opencode_from_npm_package(monkeypatch: pytest.MonkeyPatch) -> None:
    npm_calls: list[tuple[str | None, str]] = []

    def fake_npm(requested: str | None, package_name: str) -> str:
        npm_calls.append((requested, package_name))
        return f"resolved-{package_name}"

    monkeypatch.setenv("OPENCODE_VERSION", "latest")
    monkeypatch.setattr(stage, "_resolve_npm_version", fake_npm)
    monkeypatch.setattr(stage, "_resolve_codex_version", lambda _value: "codex")
    monkeypatch.setattr(stage, "_resolve_claude_code_version", lambda _value: "claude")
    monkeypatch.setattr(stage, "_resolve_pypi_version", lambda _value, package: f"resolved-{package}")
    monkeypatch.setattr(stage, "_resolve_version", lambda _value, repo: f"resolved-{repo}")

    resolved = stage._resolve_versions()

    assert ("latest", "opencode-ai") in npm_calls
    assert resolved["OPENCODE_VER"] == "resolved-opencode-ai"
