"""Focused regression tests for install_ai_cli_tools.py.

Uses mocks and temp paths; never reaches the network or installs packages.
"""
from __future__ import annotations

import json
import os
import re
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
    install_codex,
    install_opencode,
    opencode_platform_package,
    main,
    is_enabled,
    parse_tool_entries,
    verify_codex_install,
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


# The npm tree `npm install -g @openai/codex@<ver>` leaves behind, built as a fake root:
# prefix/lib/node_modules/@openai/codex/node_modules/@openai/codex-<os>-<cpu>/vendor/<triple>/
CODEX_TRIPLE = "x86_64-unknown-linux-musl"
CODEX_PLATFORM = "codex-linux-x64"


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _fake_codex_tree(
    prefix: Path,
    *,
    version: str = "0.144.0",
    vendor_version: str | None = None,
    target: str = CODEX_TRIPLE,
    vendor_dir_name: str = CODEX_TRIPLE,
    platform_name: str = CODEX_PLATFORM,
    platform_os: list[str] | None = None,
    platform_cpu: list[str] | None = None,
    native_version: str | None = None,
    launcher_triple: str = CODEX_TRIPLE,
    launcher: str | None = "mimic",
) -> Path:
    """Write the layout the Codex npm package installs under *prefix*; returns the vendor dir.

    `launcher` is the body of `prefix/bin/codex`: "mimic" (default) is a stand-in for `codex.js` that
    selects the vendor directory named by *launcher_triple* (the target the real launcher derives from
    the host) and runs that native binary, a failure like the real "Missing optional dependency" when
    the platform package has no such directory; `None` writes no launcher; any other string is a
    verbatim script body.
    """
    os_name = platform_os if platform_os is not None else ["linux"]
    cpu_name = platform_cpu if platform_cpu is not None else ["x64"]
    package = prefix / "lib" / "node_modules" / "@openai" / "codex"
    _write_json(package / "package.json", {"name": "@openai/codex", "version": version})
    platform_package = package / "node_modules" / "@openai" / platform_name
    _write_json(
        platform_package / "package.json",
        {
            "name": "@openai/codex",
            "version": f"{version}-{platform_name.removeprefix('codex-')}",
            "os": os_name,
            "cpu": cpu_name,
        },
    )
    vendor = platform_package / "vendor" / vendor_dir_name
    _write_json(
        vendor / "codex-package.json",
        {
            "layoutVersion": 1,
            "version": vendor_version if vendor_version is not None else version,
            "target": target,
            "variant": "codex",
            "entrypoint": "bin/codex",
            "resourcesDir": "codex-resources",
            "pathDir": "codex-path",
        },
    )
    for binary in ("codex", "codex-code-mode-host"):
        path = vendor / "bin" / binary
        path.parent.mkdir(parents=True, exist_ok=True)
        if binary == "codex":
            shown = native_version if native_version is not None else version
            path.write_text(f'#!/bin/sh\necho "codex-cli {shown}"\n', encoding="utf-8")
        else:
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    if launcher is not None:
        body = launcher
        if launcher == "mimic":
            root = package
            body = (
                f'for native in "{root}"/node_modules/@openai/codex-*/vendor/{launcher_triple}/bin/codex '
                f'"{root.parent}"/codex-*/vendor/{launcher_triple}/bin/codex; do\n'
                '  [ -x "$native" ] && exec "$native" "$@"\n'
                "done\n"
                f'echo "Error: Missing optional dependency for {launcher_triple}" >&2\n'
                "exit 1\n"
            )
        entry = prefix / "bin" / "codex"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        entry.chmod(0o755)
    return vendor


def _pin_codex_to_the_test_host(monkeypatch: pytest.MonkeyPatch, machine: str = "x86_64") -> None:
    """The derivation reads the running platform; a test states which host it simulates."""
    import install_ai_cli_tools as mod

    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: machine)


# install_codex


class TestInstallCodex:
    def test_skipped_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _fake_ctx(tmp_path)
        monkeypatch.setenv("INSTALL_CODEX", "false")
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        # Should not raise even though files are absent.
        install_codex(ctx)

    def test_installs_codex_as_an_image_npm_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        _pin_codex_to_the_test_host(monkeypatch)
        recorded: list[list[str]] = []
        required: list[str] = []
        monkeypatch.setenv("NPM_CONFIG_PREFIX", str(prefix))
        monkeypatch.setattr(
            "install_ai_cli_tools.run_command", lambda argv: recorded.append(argv)
        )
        monkeypatch.setattr(
            "install_ai_cli_tools.require_command",
            lambda command, _description: required.append(command),
        )
        install_codex(_fake_ctx(tmp_path))
        assert recorded == [["npm", "install", "-g", "@openai/codex@0.144.0"]]
        assert required == ["npm"]


# verify_codex_install


class TestVerifyCodexInstall:
    """The production path is verify_codex_install; these fixtures are fake npm prefixes, no npm."""

    def test_good_layout_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        _pin_codex_to_the_test_host(monkeypatch)
        verify_codex_install(prefix, "0.144.0")  # should not raise

    def test_missing_package_fails_and_names_the_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match=r"Missing the installed Codex package: .*/@openai/codex$"):
            verify_codex_install(tmp_path / "empty-prefix", "0.144.0")

    def test_missing_companion_fails_and_names_the_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """code mode spawns codex-code-mode-host from the vendor bin/; without it code mode fails."""
        prefix = tmp_path / "npm-prefix"
        vendor = _fake_codex_tree(prefix)
        (vendor / "bin" / "codex-code-mode-host").unlink()
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Missing Codex code-mode host companion: .*bin/codex-code-mode-host"):
            verify_codex_install(prefix, "0.144.0")

    def test_missing_native_binary_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prefix = tmp_path / "npm-prefix"
        vendor = _fake_codex_tree(prefix)
        (vendor / "bin" / "codex").unlink()
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Missing Codex native binary: .*bin/codex"):
            verify_codex_install(prefix, "0.144.0")

    def test_non_executable_companion_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        vendor = _fake_codex_tree(prefix)
        (vendor / "bin" / "codex-code-mode-host").chmod(0o644)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Codex code-mode host companion is not executable"):
            verify_codex_install(prefix, "0.144.0")

    def test_companion_that_is_a_directory_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        vendor = _fake_codex_tree(prefix)
        companion = vendor / "bin" / "codex-code-mode-host"
        companion.unlink()
        companion.mkdir()
        companion.chmod(0o755)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Missing Codex code-mode host companion"):
            verify_codex_install(prefix, "0.144.0")

    def test_missing_platform_package_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """npm --omit=optional skips the platform package; codex then fails at run time."""
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, platform_os=["darwin"], platform_cpu=["arm64"])
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="installed no platform package for linux/x64"):
            verify_codex_install(prefix, "0.144.0")

    @pytest.mark.parametrize(
        ("platform_os", "platform_cpu"),
        [(["linux"], ["arm64"]), (["darwin"], ["x64"])],
        ids=["right-os-wrong-cpu", "wrong-os-right-cpu"],
    )
    def test_platform_package_must_match_both_os_and_cpu(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        platform_os: list[str],
        platform_cpu: list[str],
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, platform_os=platform_os, platform_cpu=platform_cpu)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="installed no platform package for linux/x64"):
            verify_codex_install(prefix, "0.144.0")

    def test_a_hoisted_platform_package_is_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """npm may hoist the optional package next to the root package instead of nesting it."""
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        scope = prefix / "lib" / "node_modules" / "@openai"
        (scope / "codex" / "node_modules" / "@openai" / CODEX_PLATFORM).rename(scope / CODEX_PLATFORM)
        _pin_codex_to_the_test_host(monkeypatch)
        verify_codex_install(prefix, "0.144.0")  # should not raise

    def test_an_unknown_vendor_layout_version_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        vendor = _fake_codex_tree(prefix)
        manifest = json.loads((vendor / "codex-package.json").read_text(encoding="utf-8"))
        manifest["layoutVersion"] = 2
        _write_json(vendor / "codex-package.json", manifest)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="layoutVersion 2"):
            verify_codex_install(prefix, "0.144.0")

    def test_a_self_consistent_but_wrong_linux_target_fails_at_the_launcher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A gnu tree whose directory, manifest target and files all agree passes every file check; the
        launcher (which selects the musl target on this host) is what refuses it."""
        prefix = tmp_path / "npm-prefix"
        gnu = "x86_64-unknown-linux-gnu"
        _fake_codex_tree(prefix, target=gnu, vendor_dir_name=gnu)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match=r"bin/codex --version exited 1: .*Missing optional dependency"):
            verify_codex_install(prefix, "0.144.0")

    def test_arm64_host_with_the_arm64_target_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        triple = "aarch64-unknown-linux-musl"
        _fake_codex_tree(
            prefix,
            target=triple,
            vendor_dir_name=triple,
            platform_name="codex-linux-arm64",
            platform_cpu=["arm64"],
            launcher_triple=triple,
        )
        _pin_codex_to_the_test_host(monkeypatch, machine="aarch64")
        verify_codex_install(prefix, "0.144.0")  # should not raise

    def test_arm64_host_with_an_x64_tree_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        _pin_codex_to_the_test_host(monkeypatch, machine="aarch64")
        with pytest.raises(InstallerError, match="installed no platform package for linux/arm64"):
            verify_codex_install(prefix, "0.144.0")

    def test_x64_host_with_an_arm64_target_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The platform package matches the host (x64) but ships the arm64 target the launcher won't pick."""
        prefix = tmp_path / "npm-prefix"
        triple = "aarch64-unknown-linux-musl"
        _fake_codex_tree(prefix, target=triple, vendor_dir_name=triple)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Missing optional dependency"):
            verify_codex_install(prefix, "0.144.0")

    def test_missing_launcher_fails_and_names_the_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, launcher=None)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match=r"Missing Codex launcher: .*/npm-prefix/bin/codex"):
            verify_codex_install(prefix, "0.144.0")

    def test_launcher_reporting_another_version_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Metadata can all say 0.144.0 while the binary that runs is another release."""
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, native_version="0.143.0")
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match=r"--version reported 'codex-cli 0\.143\.0', but CODEX_VER pins '0\.144\.0'"):
            verify_codex_install(prefix, "0.144.0")

    @pytest.mark.parametrize("reported", ["codex-cli 0.144.0-alpha.1", "codex-cli 10.144.0", "codex-cli 0.144.01"])
    def test_launcher_version_must_be_the_whole_pinned_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reported: str
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, launcher=f'echo "{reported}"\n')
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="but CODEX_VER pins '0.144.0'"):
            verify_codex_install(prefix, "0.144.0")

    def test_launcher_printing_nothing_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, launcher="exit 0\n")
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="--version printed nothing"):
            verify_codex_install(prefix, "0.144.0")

    def test_launcher_failing_with_the_right_version_text_still_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, launcher='echo "codex-cli 0.144.0"\nexit 7\n')
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="--version exited 7"):
            verify_codex_install(prefix, "0.144.0")

    def test_non_executable_launcher_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        (prefix / "bin" / "codex").chmod(0o644)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Codex launcher is not executable"):
            verify_codex_install(prefix, "0.144.0")

    def test_launcher_that_cannot_be_executed_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An executable file with a missing interpreter raises OSError at exec, not a non-zero exit."""
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        (prefix / "bin" / "codex").write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Could not run .*--version"):
            verify_codex_install(prefix, "0.144.0")

    def test_launcher_that_hangs_is_cut_off(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import install_ai_cli_tools as mod

        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, launcher="exec sleep 30\n")
        _pin_codex_to_the_test_host(monkeypatch)
        monkeypatch.setattr(mod, "CODEX_LAUNCHER_TIMEOUT_SECONDS", 1)
        with pytest.raises(InstallerError, match="Could not run .*timed out"):
            verify_codex_install(prefix, "0.144.0")

    def test_wrong_platform_triple_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, vendor_dir_name="aarch64-unknown-linux-musl")
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="names target 'x86_64-unknown-linux-musl'"):
            verify_codex_install(prefix, "0.144.0")

    def test_installed_version_mismatch_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, version="0.143.0")
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(
            InstallerError, match=r"package\.json records Codex version '0\.143\.0', but CODEX_VER pins '0\.144\.0'"
        ):
            verify_codex_install(prefix, "0.144.0")

    def test_vendor_manifest_version_mismatch_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The vendored payload is the binary Codex runs; the npm wrapper version alone proves nothing."""
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix, vendor_version="0.143.0")
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(
            InstallerError, match="codex-package.json records version '0.143.0', but CODEX_VER pins '0.144.0'"
        ):
            verify_codex_install(prefix, "0.144.0")

    def test_two_vendor_directories_are_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two target triples under one platform package: which one Codex runs is ambiguous."""
        prefix = tmp_path / "npm-prefix"
        vendor = _fake_codex_tree(prefix)
        second = vendor.parent / "second-triple"
        second.mkdir()
        (second / "codex-package.json").write_text(json.dumps({"layoutVersion": 1}), encoding="utf-8")
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="exactly one vendor directory"):
            verify_codex_install(prefix, "0.144.0")

    def test_an_unknown_platform_is_refused_not_guessed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        import install_ai_cli_tools as mod

        monkeypatch.setattr(mod.platform, "system", lambda: "Plan9")
        monkeypatch.setattr(mod.platform, "machine", lambda: "riscv64")
        with pytest.raises(InstallerError, match="Cannot derive the Codex platform package"):
            verify_codex_install(prefix, "0.144.0")

    def test_install_codex_fails_when_the_tree_is_incomplete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The verify step is the installer's, not an optional extra: install refuses a partial tree."""
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        monkeypatch.setenv("NPM_CONFIG_PREFIX", str(tmp_path / "npm-prefix"))
        monkeypatch.setattr("install_ai_cli_tools.run_command", lambda argv: None)
        monkeypatch.setattr("install_ai_cli_tools.require_command", lambda *a, **k: None)
        _pin_codex_to_the_test_host(monkeypatch)
        with pytest.raises(InstallerError, match="Missing the installed Codex package"):
            install_codex(_fake_ctx(tmp_path))

    def test_install_codex_never_copies_the_tree_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`codex update` rewrites the npm tree; a copied companion would drift from `codex`."""
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        prefix = tmp_path / "npm-prefix"
        _fake_codex_tree(prefix)
        monkeypatch.setenv("NPM_CONFIG_PREFIX", str(prefix))
        monkeypatch.setattr("install_ai_cli_tools.run_command", lambda argv: None)
        monkeypatch.setattr("install_ai_cli_tools.require_command", lambda *a, **k: None)
        _pin_codex_to_the_test_host(monkeypatch)
        before = sorted(path.relative_to(prefix).as_posix() for path in prefix.rglob("*"))
        install_codex(_fake_ctx(tmp_path))
        assert sorted(path.relative_to(prefix).as_posix() for path in prefix.rglob("*")) == before

    def test_install_codex_refuses_to_verify_an_unknown_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        monkeypatch.delenv("NPM_CONFIG_PREFIX", raising=False)
        monkeypatch.setattr("install_ai_cli_tools.run_command", lambda argv: None)
        monkeypatch.setattr("install_ai_cli_tools.require_command", lambda *a, **k: None)
        with pytest.raises(InstallerError, match="NPM_CONFIG_PREFIX is not set"):
            install_codex(_fake_ctx(tmp_path))

    def test_the_dockerfile_does_not_copy_the_companion_out(self) -> None:
        dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
        assert "codex-code-mode-host" not in dockerfile


# install_opencode


class TestInstallOpencode:
    def test_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INSTALL_OPENCODE", "false")
        install_opencode()  # should not raise

    def test_command_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the npm command that would be issued."""
        monkeypatch.setenv("OPENCODE_VER", "1.2.3")
        monkeypatch.setenv("INSTALL_OPENCODE", "true")
        monkeypatch.setenv("NPM_CONFIG_PREFIX", "/opt/mdt/npm")

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
        assert recorded[0] == ["npm", "install", "-g", "@opencode/cli-linux-x64-baseline@1.2.3"]
        assert required == ["npm", "opencode"]
        assert linked == [(
            Path("/opt/mdt/npm/lib/node_modules/@opencode/cli-linux-x64-baseline/bin/opencode"),
            Path("/opt/mdt/npm/bin/opencode"),
        )]

def test_user_mode_uses_the_callers_npm_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools_file = tmp_path / "tools"
    tools_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("TOOLS_FILE", str(tools_file))
    monkeypatch.setenv("NPM_CONFIG_PREFIX", "/chosen/prefix")
    monkeypatch.setenv("PATH", "/usr/bin")

    assert main(["user"]) == 0
    assert os.environ["NPM_CONFIG_PREFIX"] == "/chosen/prefix"
    assert os.environ["PATH"].startswith("/chosen/prefix/bin:")


def test_user_mode_refuses_to_guess_the_npm_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools_file = tmp_path / "tools"
    tools_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("TOOLS_FILE", str(tools_file))
    monkeypatch.delenv("NPM_CONFIG_PREFIX", raising=False)

    with pytest.raises(InstallerError, match="NPM_CONFIG_PREFIX"):
        main(["user"])
    assert "NPM_CONFIG_PREFIX" not in os.environ


def test_standalone_binaries_are_group_writable_in_the_mdt_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import install_ai_cli_tools as mod

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    archive = downloads / "antigravity-4.5.6.tar.gz"
    _make_tar_with_binaries(archive, "antigravity")
    bin_dir = tmp_path / "opt-mdt-bin"
    monkeypatch.setattr(mod, "IMAGE_BIN_DIR", bin_dir)
    monkeypatch.setenv("ANTIGRAVITY_VER", "4.5.6")
    monkeypatch.delenv("INSTALL_ANTIGRAVITY", raising=False)

    mod.install_antigravity(_fake_ctx(tmp_path))

    for name in ("antigravity", "agy"):
        assert (bin_dir / name).stat().st_mode & 0o777 == 0o775, name


def test_claude_is_installed_through_npm_into_the_mdt_prefix_not_as_a_copied_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The native/copied binary shadowed the update the tool wrote into ~/.local: updates
    silently never took effect. The npm-global install updates itself in NPM_CONFIG_PREFIX."""
    import install_ai_cli_tools as mod

    calls: list[list[str]] = []
    monkeypatch.setattr(mod, "run_command", lambda command, *a, **k: calls.append(list(command)))
    monkeypatch.setattr(mod, "require_command", lambda *a, **k: None)
    monkeypatch.setenv("CLAUDE_CODE_VER", "1.2.3")
    monkeypatch.delenv("INSTALL_CLAUDE_CODE", raising=False)

    mod.install_claude(_fake_ctx(tmp_path))

    assert calls == [["npm", "install", "-g", "@anthropic-ai/claude-code@1.2.3"]]
    listed = dict(mod.parse_tool_entries(Path(__file__).resolve().parents[1] / "ai-cli-tools.list"))
    assert listed["claude"] == "npm-image", "claude must be installed in the npm-image step, not copied by the root step"


@pytest.mark.parametrize("value", [None, "", "  ", "latest"])
def test_claude_requires_the_staged_concrete_version_and_never_installs_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """Without CLAUDE_CODE_VER the Dockerfile ARG used to supply "latest", so npm installed
    @latest, an unrecorded version (the image must ship a pin it recorded)."""
    import install_ai_cli_tools as mod

    calls: list[list[str]] = []
    monkeypatch.setattr(mod, "run_command", lambda command, *a, **k: calls.append(list(command)))
    monkeypatch.setattr(mod, "require_command", lambda *a, **k: None)
    monkeypatch.delenv("INSTALL_CLAUDE_CODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_VERSION", raising=False)  # the build ARG must not stand in for it
    if value is None:
        monkeypatch.delenv("CLAUDE_CODE_VER", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CODE_VER", value)

    with pytest.raises(mod.InstallerError, match="CLAUDE_CODE_VER"):
        mod.install_claude(_fake_ctx(tmp_path))

    assert calls == []


def test_the_build_fails_when_a_manifest_probe_cannot_report_a_version() -> None:
    """The manifest used to record `codex=$(codex --version | head -1)`, which always succeeded:
    a broken Codex (or Claude) shipped an error string as its version. Each probe must fail the
    build on a non-zero exit or empty output, and `| head -1` must not hide the exit status."""
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    for tool in ("claude", "codex"):
        assert f'echo "{tool}=$({tool} --version 2>&1 | head -1)"' not in dockerfile, tool
        probe = f'v="$({tool} --version 2>&1)" || {{ echo "[ERROR] {tool} --version failed" >&2; exit 1; }}'
        assert probe in dockerfile, f"{tool} must abort the build on a failed --version"
        empty = f'test -n "${{v}}" || {{ echo "[ERROR] {tool} --version printed nothing" >&2; exit 1; }}'
        assert empty in dockerfile, f"{tool} must abort the build on empty output"
        assert f'echo "{tool}=${{v%%$\'\\n\'*}}"' in dockerfile, f"{tool} records the first line of the probe"


def test_the_build_has_no_latest_default_for_claude() -> None:
    root = Path(__file__).resolve().parents[1]
    bake = (root / "docker-bake.hcl").read_text(encoding="utf-8")
    block = bake[bake.index('variable "CLAUDE_CODE_VERSION"'):]
    assert 'default = ""' in block.split("}", 1)[0]
    assert re.search(r'^ARG CLAUDE_CODE_VERSION\s*$', (root / "Dockerfile").read_text(encoding="utf-8"), re.MULTILINE)


def test_dockerfile_constructs_the_mdt_tree_before_any_content() -> None:
    """The group contract is a construction rule: roots first, umask on every writer."""
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    group_run = dockerfile.index('groupadd -g "${MDT_GID}" mdt')
    assert group_run < dockerfile.index("/tmp/mdt-inputs/install_ai_cli_tools.py root")
    assert group_run < dockerfile.index("python3 -m venv /opt/mdt/venv")
    assert group_run < dockerfile.index("/tmp/mdt-inputs/install_pwmcp_instance.py \\")
    assert "python3 -m venv /opt/mdt/venv" in dockerfile
    assert "/opt/mdt/venvs/${venv_name}" in dockerfile
    assert 'ENV UV_PYTHON_INSTALL_DIR="/opt/mdt/python"' in dockerfile
    assert 'ENV NPM_CONFIG_PREFIX="/opt/mdt/npm"' in dockerfile
    assert 'remaining="$(find "${home}" -mindepth 1 -maxdepth 1 -print)"' in dockerfile
    assert "RUN mdt-perms check /opt/mdt /srv" in dockerfile
    assert "/home/vscode/.venv" not in dockerfile
    # No recursive ownership pass over earlier-layer content: the only recursive
    # operation on the mdt trees is the fix-up of Oh My Zsh, which the SAME RUN
    # copies in (an overlayfs chown/chmod -R on older content copies it all up).
    recursive = re.findall(
        r"(?:chgrp|chmod|chown)\s+-[A-Za-z]*R[^;\n]*?(?:/opt/mdt|/srv)[^\s;]*", dockerfile
    )
    assert recursive == ["chown -R root:mdt /opt/mdt/oh-my-zsh"]


def test_dockerfile_sets_the_final_mode_of_every_root_after_assigning_the_group() -> None:
    """First real build: install -d does not re-mode an existing directory and chgrp can
    drop setgid, so each RUN must end with an explicit chmod 2775 and verify every root."""
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    start = dockerfile.index('groupadd -g "${MDT_GID}" mdt')
    group_run = dockerfile[start: dockerfile.index("\n\n", start)]
    assert group_run.index("chgrp mdt ${roots}") < group_run.index("chmod 2775 ${roots}")
    assert "2775 root:mdt" in group_run.split("chmod 2775 ${roots}", 1)[1], "roots must be verified after the chmod"


def test_dockerfile_claims_srv_after_the_last_apt_step() -> None:
    """Second real-build finding: base-files' postinst re-modes /srv to 755 whenever apt
    reconfigures it, so /srv must be claimed after every apt step, before the PWMCP install."""
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    claim = dockerfile.index("chmod 2775 /srv")
    assert claim > dockerfile.rindex("apt-get install"), "an apt step after the claim can undo it"
    assert claim < dockerfile.index("/tmp/mdt-inputs/install_pwmcp_instance.py \\")
    assert 'roots="' in dockerfile and "/srv" not in dockerfile.split('roots="', 1)[1].split('"', 1)[0]


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
    from test_oci_tool_staging import fake_version_records

    monkeypatch.setattr(stage, "_load_version_records", lambda path=None: fake_version_records())
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
    monkeypatch.setattr(stage, "_resolve_latest_aged_release", lambda repo, **_k: f"resolved-{repo}")
    monkeypatch.setattr(stage, "_resolve_sss_commit", lambda _value: "e" * 40)

    resolved = stage._resolve_versions()

    assert (None, "@opencode/cli") in npm_calls
    assert resolved["OPENCODE_VER"] == "resolved-@opencode/cli"


def test_a_build_arg_pin_cannot_stand_in_for_the_staged_claude_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only tool-versions.env (written by staging after the age check) may supply the version."""
    import install_ai_cli_tools as mod

    monkeypatch.setattr(mod, "run_command", lambda *a, **k: pytest.fail("must not install"))
    monkeypatch.setattr(mod, "require_command", lambda *a, **k: None)
    monkeypatch.delenv("INSTALL_CLAUDE_CODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_VER", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_VERSION", "2.1.302")

    with pytest.raises(mod.InstallerError, match="CLAUDE_CODE_VER"):
        mod.install_claude(_fake_ctx(tmp_path))


# ── M1: every npm tool of the R16 policy installs only a staged, exact, recorded version ──

STAGED_INSTALLERS = [
    ("install_codex", "CODEX_VER", "CODEX_VERSION", "INSTALL_CODEX", True),
    ("install_reasonix", "REASONIX_VER", "REASONIX_VERSION", "INSTALL_REASONIX", False),
    ("install_pi", "PI_VER", "PI_VERSION", "INSTALL_PI", False),
    ("install_opencode", "OPENCODE_VER", "OPENCODE_VERSION", "INSTALL_OPENCODE", False),
]


@pytest.mark.parametrize("value", [None, "", "  ", "latest"])
@pytest.mark.parametrize("function,ver,arg,switch,takes_ctx", STAGED_INSTALLERS)
def test_the_npm_tools_refuse_an_unstaged_or_latest_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, function: str, ver: str, arg: str, switch: str,
    takes_ctx: bool, value: str | None,
) -> None:
    import install_ai_cli_tools as mod

    calls: list[list[str]] = []
    monkeypatch.setattr(mod, "run_command", lambda command, *a, **k: calls.append(list(command)))
    monkeypatch.setattr(mod, "require_command", lambda *a, **k: None)
    monkeypatch.delenv(switch, raising=False)
    monkeypatch.setenv(arg, "9.9.9")  # a build ARG must not stand in for the staged value
    if value is None:
        monkeypatch.delenv(ver, raising=False)
    else:
        monkeypatch.setenv(ver, value)

    args = (_fake_ctx(tmp_path),) if takes_ctx else ()
    with pytest.raises(mod.InstallerError, match=ver):
        getattr(mod, function)(*args)

    assert calls == []


def test_the_dockerfile_has_no_latest_default_for_the_staged_npm_tools() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    for arg in ("REASONIX_VERSION", "OPENCODE_VERSION", "PI_VERSION"):
        assert re.search(rf"^ARG {arg}\s*$", dockerfile, re.MULTILINE), f"ARG {arg} must have no default"
    assert not re.search(r'^ARG (?:REASONIX|OPENCODE|PI)_VERSION="latest"', dockerfile, re.MULTILINE)
