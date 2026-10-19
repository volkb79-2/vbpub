"""Focused regression tests for install_ai_cli_tools.py.

Uses mocks and temp paths; never reaches the network or installs packages.
"""
from __future__ import annotations

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

    def test_installs_codex_as_an_image_npm_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_VER", "0.144.0")
        recorded: list[list[str]] = []
        required: list[str] = []
        monkeypatch.setattr(
            "install_ai_cli_tools.run_command", lambda argv: recorded.append(argv)
        )
        monkeypatch.setattr(
            "install_ai_cli_tools.require_command",
            lambda command, _description: required.append(command),
        )
        install_codex(_fake_ctx(Path("/tmp")))
        assert recorded == [["npm", "install", "-g", "@openai/codex@0.144.0"]]
        assert required == ["npm"]


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
        assert recorded[0] == ["npm", "install", "-g", "opencode-linux-x64-baseline@1.2.3"]
        assert required == ["npm", "opencode"]
        assert linked == [(
            Path("/opt/mdt/npm/lib/node_modules/opencode-linux-x64-baseline/bin/opencode"),
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
    assert group_run < dockerfile.index("/tmp/install_ai_cli_tools.py root")
    assert group_run < dockerfile.index("python3 -m venv /opt/mdt/venv")
    assert group_run < dockerfile.index("/tmp/install_pwmcp_instance.py \\")
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
    assert claim < dockerfile.index("/tmp/install_pwmcp_instance.py \\")
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

    resolved = stage._resolve_versions()

    assert (None, "opencode-ai") in npm_calls
    assert resolved["OPENCODE_VER"] == "resolved-opencode-ai"


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
