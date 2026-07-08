#!/usr/bin/env python3
"""Install the optional AI CLI tools used by this image family.

The build pipeline stages release artifacts into /tmp/tool-artifacts-staging and
describes the desired tool set in /tmp/ai-cli-tools.list. This helper applies
the requested subset for the current install mode ("root" or "venv").
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TOOLS_FILE = Path("/tmp/ai-cli-tools.list")
DEFAULT_DOWNLOADS_DIR = Path("/tmp/tool-artifacts-staging/downloads")
DEFAULT_VENV_PYTHON = Path("/home/vscode/.venv/bin/python")


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


def sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_checksum_entry(path: Path, expected_name: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        checksum, name = parts[0].lower(), parts[1].lstrip("*")
        if name == expected_name:
            return checksum
    raise InstallerError(
        f"Missing checksum entry for {expected_name} in {path}"
    )


def copy_binary(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o755)


def find_binary(root: Path, binary_name: str) -> Path:
    for candidate in root.rglob(binary_name):
        if candidate.is_file() and (candidate.stat().st_mode & 0o111):
            return candidate
    raise InstallerError(f"Failed to locate {binary_name} in extracted archive")


def install_binary_from_archive(archive: Path, binary_name: str, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix=f"{binary_name}-extract-") as temp_dir:
        extract_root = Path(temp_dir)
        try:
            shutil.unpack_archive(str(archive), extract_root)
        except (shutil.ReadError, ValueError) as exc:
            raise InstallerError(f"Failed to extract archive: {archive}") from exc
        extracted_binary = find_binary(extract_root, binary_name)
        copy_binary(extracted_binary, destination)


def install_codex(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_CODEX", True):
        log("INFO", "INSTALL_CODEX=false; skipping Codex")
        return

    version = env_value("CODEX_VER", "CODEX_VERSION", default="latest")
    archive = ctx.downloads_dir / f"codex-{version}.tar.gz"
    checksums = ctx.downloads_dir / f"codex-{version}-SHA256SUMS"

    require_file(archive, "staged Codex archive")
    require_file(checksums, "staged Codex checksums")

    expected_sha = read_checksum_entry(
        checksums,
        "codex-package-x86_64-unknown-linux-musl.tar.gz",
    )
    actual_sha = sha256_hex(archive)
    if actual_sha != expected_sha:
        raise InstallerError(
            f"Codex checksum mismatch: expected {expected_sha}, got {actual_sha}"
        )

    install_binary_from_archive(archive, "codex", Path("/usr/local/bin/codex"))


def install_claude(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_CLAUDE_CODE", True):
        log("INFO", "INSTALL_CLAUDE_CODE=false; skipping Claude Code")
        return

    version = env_value("CLAUDE_CODE_VER", "CLAUDE_CODE_VERSION", default="latest")
    binary = ctx.downloads_dir / f"claude-{version}-linux-x64"
    require_file(binary, "staged Claude Code binary")
    copy_binary(binary, Path("/usr/local/bin/claude"))


def install_antigravity(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_ANTIGRAVITY", True):
        log("INFO", "INSTALL_ANTIGRAVITY=false; skipping Antigravity")
        return

    version = env_value("ANTIGRAVITY_VER", "ANTIGRAVITY_VERSION", default="latest")
    archive = ctx.downloads_dir / f"antigravity-{version}.tar.gz"
    require_file(archive, "staged Antigravity archive")

    install_binary_from_archive(archive, "antigravity", Path("/usr/local/bin/antigravity"))
    copy_binary(Path("/usr/local/bin/antigravity"), Path("/usr/local/bin/agy"))


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
    else:
        log("WARN", f"Unknown AI CLI tool in {ctx.tools_file}: {tool}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install optional AI CLI tools")
    parser.add_argument("mode", choices=("root", "venv"))
    args = parser.parse_args(argv)

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
