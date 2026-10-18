#!/usr/bin/env python3
"""Install the optional AI CLI tools used by this image family.

The build pipeline stages release artifacts into /tmp/tool-artifacts-staging and
describes the desired tool set in /tmp/ai-cli-tools.list. This helper applies
the requested subset for the current install mode ("root", "user", or "venv").
"""
from __future__ import annotations

import argparse
from mdt_cli import MdtArgumentParser
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TOOLS_FILE = Path("/tmp/ai-cli-tools.list")
DEFAULT_DOWNLOADS_DIR = Path("/tmp/tool-artifacts-staging/downloads")
DEFAULT_VENV_PYTHON = Path("/opt/mdt/venv/bin/python")
# Image-managed standalone binaries live in the `mdt` group tree, ahead of
# everything else on PATH (D4/D5). Group-writable so the runtime user can replace
# them in place; the Dockerfile RUN creates /opt/mdt/bin `root:mdt 2775`.
IMAGE_BIN_DIR = Path("/opt/mdt/bin")
IMAGE_BINARY_MODE = 0o775


class InstallerError(RuntimeError):
    """Raised when AI CLI tool installation fails."""


@dataclass(frozen=True)
class InstallerContext:
    tools_file: Path
    downloads_dir: Path
    venv_python: Path


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}", file=sys.stderr)


def is_enabled(name: str, default: bool = True) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


def env_value(*names: str, default: str = "") -> str:
    for name in names:
        raw_value = os.getenv(name)
        if raw_value is None:
            continue
        value = raw_value.strip()
        if value:
            return value
    return default


def env_path(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip()
    if not value:
        return default
    return Path(value)


def require_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise InstallerError(f"Missing {description}: {path}")


def require_command(command: str, description: str) -> None:
    if shutil.which(command) is None:
        raise InstallerError(description)


def run_command(argv: list[str]) -> None:
    try:
        subprocess.run(argv, check=True)
    except FileNotFoundError as exc:
        raise InstallerError(f"Missing required command: {argv[0]}") from exc
    except subprocess.CalledProcessError as exc:
        rendered = " ".join(argv)
        raise InstallerError(f"Command failed with exit code {exc.returncode}: {rendered}") from exc


def copy_binary(source: Path, destination: Path, mode: int = 0o755) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(mode)


def link_binary(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    destination.symlink_to(os.path.relpath(source, destination.parent))


def find_binary(root: Path, binary_name: str) -> Path:
    for candidate in root.rglob(binary_name):
        if candidate.is_file() and (candidate.stat().st_mode & 0o111):
            return candidate
    raise InstallerError(f"Failed to locate {binary_name} in extracted archive")


def install_binary_from_archive(
    archive: Path, binary_name: str, destination: Path, mode: int = 0o755
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"{binary_name}-extract-") as temp_dir:
        extract_root = Path(temp_dir)
        try:
            shutil.unpack_archive(str(archive), extract_root)
        except (shutil.ReadError, ValueError) as exc:
            raise InstallerError(f"Failed to extract archive: {archive}") from exc
        extracted_binary = find_binary(extract_root, binary_name)
        copy_binary(extracted_binary, destination, mode)


def install_binaries_from_archive(archive: Path, *pairs: tuple[str, Path]) -> None:
    """Extract *archive* once and copy each named binary to its destination."""
    with tempfile.TemporaryDirectory(prefix="archive-extract-") as temp_dir:
        extract_root = Path(temp_dir)
        try:
            shutil.unpack_archive(str(archive), extract_root)
        except (shutil.ReadError, ValueError) as exc:
            raise InstallerError(f"Failed to extract archive: {archive}") from exc
        for binary_name, destination in pairs:
            extracted_binary = find_binary(extract_root, binary_name)
            copy_binary(extracted_binary, destination)


def install_codex(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_CODEX", True):
        log("INFO", "INSTALL_CODEX=false; skipping Codex")
        return

    version = env_value("CODEX_VER", "CODEX_VERSION", default="latest")
    require_command("npm", "npm is required to install Codex")
    run_command(["npm", "install", "-g", f"@openai/codex@{version}"])


def install_claude(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_CLAUDE_CODE", True):
        log("INFO", "INSTALL_CLAUDE_CODE=false; skipping Claude Code")
        return

    # Installed through npm into NPM_CONFIG_PREFIX (/opt/mdt/npm), the officially
    # documented npm-global install of Claude Code. Its `claude update` and background
    # updater then run `npm install -g` into that group-writable prefix; the native
    # installer writes ~/.local/share/claude and ~/.local/bin/claude instead, which the
    # image copy used to shadow, so updates silently never took effect.
    # REQUIRED and only from the staged value: staging resolved the exact version that is
    # recorded in tool-versions.env and the image manifest, so an absent value must not let npm
    # install an unrecorded `@latest`.
    version = env_value("CLAUDE_CODE_VER")
    if not version or version == "latest":
        raise InstallerError(
            "CLAUDE_CODE_VER is not set to a concrete version (the staged, recorded "
            "value from tool-versions.env); refusing to install Claude Code @latest"
        )
    require_command("npm", "npm is required to install Claude Code")
    run_command(["npm", "install", "-g", f"@anthropic-ai/claude-code@{version}"])


def install_antigravity(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_ANTIGRAVITY", True):
        log("INFO", "INSTALL_ANTIGRAVITY=false; skipping Antigravity")
        return

    version = env_value("ANTIGRAVITY_VER", "ANTIGRAVITY_VERSION", default="latest")
    archive = ctx.downloads_dir / f"antigravity-{version}.tar.gz"
    require_file(archive, "staged Antigravity archive")

    install_binary_from_archive(
        archive, "antigravity", IMAGE_BIN_DIR / "antigravity", IMAGE_BINARY_MODE
    )
    copy_binary(IMAGE_BIN_DIR / "antigravity", IMAGE_BIN_DIR / "agy", IMAGE_BINARY_MODE)


def install_reasonix() -> None:
    if not is_enabled("INSTALL_REASONIX", True):
        log("INFO", "INSTALL_REASONIX=false; skipping Reasonix")
        return

    version = env_value("REASONIX_VER", "REASONIX_VERSION", default="latest")
    require_command("npm", "npm is required to install Reasonix")
    run_command(["npm", "install", "-g", f"reasonix@{version}"])


def install_openclaw() -> None:
    if not is_enabled("INSTALL_OPENCLAW", True):
        log("INFO", "INSTALL_OPENCLAW=false; skipping OpenClaw")
        return

    version = env_value("OPENCLAW_VER", "OPENCLAW_VERSION", default="latest")
    require_command("npm", "npm is required to install OpenClaw")
    run_command(["npm", "install", "-g", f"openclaw@{version}"])


def install_copilot() -> None:
    if not is_enabled("INSTALL_COPILOT", True):
        log("INFO", "INSTALL_COPILOT=false; skipping GitHub Copilot CLI")
        return

    version = env_value("COPILOT_VER", "COPILOT_VERSION", default="latest")
    require_command("npm", "npm is required to install GitHub Copilot CLI")
    run_command(["npm", "install", "-g", f"@github/copilot@{version}"])


def install_claudelink() -> None:
    if not is_enabled("INSTALL_CLAUDELINK", True):
        log("INFO", "INSTALL_CLAUDELINK=false; skipping ClaudeLink")
        return

    version = env_value("CLAUDELINK_VER", "CLAUDELINK_VERSION", default="latest")
    require_command("npm", "npm is required to install ClaudeLink")
    run_command(["npm", "install", "-g", f"claudelink@{version}"])


def install_pi() -> None:
    if not is_enabled("INSTALL_PI", True):
        log("INFO", "INSTALL_PI=false; skipping Pi")
        return

    version = env_value("PI_VER", "PI_VERSION", default="latest")
    require_command("npm", "npm is required to install Pi")
    run_command(["npm", "install", "-g", f"@earendil-works/pi-coding-agent@{version}"])


def install_aider(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_AIDER", True):
        log("INFO", "INSTALL_AIDER=false; skipping Aider")
        return

    version = env_value("AIDER_VER", "AIDER_VERSION", default="main")
    if version == "main":
        spec = "aider-chat @ git+https://github.com/Aider-AI/aider.git@main"
    elif version == "latest":
        spec = "aider-chat"
    else:
        spec = f"aider-chat=={version}"

    run_command(
        [
            str(ctx.venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            spec,
        ]
    )


def opencode_platform_package() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        architecture = "x64-baseline"
    elif machine in {"aarch64", "arm64"}:
        architecture = "arm64"
    else:
        raise InstallerError(f"Unsupported OpenCode architecture: {machine}")

    libc_name = platform.libc_ver()[0].lower()
    libc_suffix = "-musl" if libc_name == "musl" else ""
    return f"opencode-linux-{architecture}{libc_suffix}"


def install_opencode() -> None:
    if not is_enabled("INSTALL_OPENCODE", True):
        log("INFO", "INSTALL_OPENCODE=false; skipping OpenCode")
        return

    version = env_value("OPENCODE_VER", "OPENCODE_VERSION", default="latest")
    require_command("npm", "npm is required to install OpenCode")
    package = opencode_platform_package()
    run_command(["npm", "install", "-g", f"{package}@{version}"])

    prefix = Path(env_value("NPM_CONFIG_PREFIX", default="/opt/mdt/npm"))
    binary = prefix / "lib" / "node_modules" / package / "bin" / "opencode"
    require_file(binary, "OpenCode platform binary")
    link_binary(binary, prefix / "bin" / "opencode")
    require_command("opencode", "OpenCode platform package did not expose the opencode command")


def parse_tool_entries(tools_file: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in tools_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.partition("#")[0].strip()
        if not line:
            continue
        tool_part, separator, mode_part = line.partition("|")
        if not separator:
            continue
        tool = tool_part.strip()
        mode = mode_part.strip()
        if not tool or not mode:
            continue
        entries.append((tool, mode))
    return entries


def install_tool(tool: str, ctx: InstallerContext) -> None:
    if tool == "codex":
        install_codex(ctx)
    elif tool == "claude":
        install_claude(ctx)
    elif tool == "antigravity":
        install_antigravity(ctx)
    elif tool == "reasonix":
        install_reasonix()
    elif tool == "openclaw":
        install_openclaw()
    elif tool == "copilot":
        install_copilot()
    elif tool == "aider":
        install_aider(ctx)
    elif tool == "opencode":
        install_opencode()
    elif tool == "claudelink":
        install_claudelink()
    elif tool == "pi":
        install_pi()
    else:
        log("WARN", f"Unknown AI CLI tool in {ctx.tools_file}: {tool}")


def main(argv: list[str] | None = None) -> int:
    parser = MdtArgumentParser(description="Install optional AI CLI tools")
    parser.add_argument("mode", choices=("root", "npm-image", "user", "venv"))
    args = parser.parse_args(argv)

    if args.mode == "user":
        # The prefix is a fact of the target environment (the image ENV, or the
        # caller's choice), never guessed here: a wrong guess would install into
        # the wrong tree and still "succeed".
        npm_prefix = os.environ.get("NPM_CONFIG_PREFIX", "").strip()
        if not npm_prefix:
            raise InstallerError("user mode requires NPM_CONFIG_PREFIX to be set")
        os.environ["PATH"] = f"{npm_prefix}/bin:{os.environ.get('PATH', '')}"
    elif args.mode in {"root", "npm-image"}:
        # Image packages live in the `mdt` group tree outside the mounted home.
        npm_prefix = os.environ.setdefault("NPM_CONFIG_PREFIX", "/opt/mdt/npm")
        os.environ["PATH"] = f"{npm_prefix}/bin:{os.environ.get('PATH', '')}"

    ctx = InstallerContext(
        tools_file=env_path("TOOLS_FILE", DEFAULT_TOOLS_FILE),
        downloads_dir=env_path("DOWNLOADS_DIR", DEFAULT_DOWNLOADS_DIR),
        venv_python=env_path("VENV_PYTHON", DEFAULT_VENV_PYTHON),
    )

    if not ctx.tools_file.is_file():
        raise InstallerError(f"Missing AI CLI tool list: {ctx.tools_file}")

    for tool, mode in parse_tool_entries(ctx.tools_file):
        if mode != args.mode:
            continue
        install_tool(tool, ctx)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallerError as exc:
        log("ERROR", str(exc))
        raise SystemExit(1)
