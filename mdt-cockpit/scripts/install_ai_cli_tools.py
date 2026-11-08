#!/usr/bin/env python3
"""Install the optional AI CLI tools used by this image family.

The build pipeline stages release artifacts into /tmp/tool-artifacts-staging and
describes the desired tool set in /tmp/ai-cli-tools.list. This helper applies
the requested subset for the current install mode ("root", "user", or "venv").
"""
from __future__ import annotations

import argparse
from mdt_cli import MdtArgumentParser
import json
import os
import platform
import re
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


# An exact release number: starts with a digit, no spaces, no URL/ref forms - so neither `latest` nor `main`
# nor a git ref can be installed in place of a recorded version.
VERSION_SHAPE = re.compile(r"[0-9][0-9A-Za-z.+_\-]*")


COMMIT_SHAPE = re.compile(r"[0-9a-f]{40}")


BEFORE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def staged_before(variable: str, label: str) -> str:
    """The age cutoff staging recorded for a vetted tool; installs hold ALL dependencies to it (npm --before)."""
    before = env_value(variable)
    if not BEFORE_SHAPE.fullmatch(before):
        raise InstallerError(
            f"{variable} is not a staged UTC cutoff like 2026-10-05T00:00:00Z (from tool-versions.env); "
            f"refusing to install {label} with floating dependencies"
        )
    return before


def staged_version(variable: str, label: str) -> str:
    """The exact version staging resolved and recorded in tool-versions.env.

    REQUIRED: an absent, empty or `latest` value must not let npm install an unrecorded
    `@latest`, so the image would ship a version nothing recorded.
    """
    version = env_value(variable)
    if not VERSION_SHAPE.fullmatch(version):
        raise InstallerError(
            f"{variable} is not set to a concrete version (the staged, recorded value from "
            f"tool-versions.env); refusing to install {label} @latest"
        )
    return version


def install_codex(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_CODEX", True):
        log("INFO", "INSTALL_CODEX=false; skipping Codex")
        return

    version = staged_version("CODEX_VER", "Codex")
    require_command("npm", "npm is required to install Codex")
    run_command(["npm", "install", "-g", f"@openai/codex@{version}"])
    # The prefix is a fact of the target environment (every install mode sets it before this runs);
    # never guessed, because a wrong guess would verify a different tree and still "pass".
    prefix = env_value("NPM_CONFIG_PREFIX")
    if not prefix:
        raise InstallerError(
            "NPM_CONFIG_PREFIX is not set; refusing to verify a Codex install in an unknown prefix"
        )
    verify_codex_install(Path(prefix), version)


# Node names the platform package after these values in its package.json `os`/`cpu` arrays.
CODEX_OS_NAMES = {"Linux": "linux", "Darwin": "darwin", "Windows": "win32"}
CODEX_CPU_NAMES = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}


def codex_platform_package_dir(package: Path) -> Path:
    """The platform package the Codex launcher selects, derived from what the packages declare.

    The root package names its optional platform dependencies `@openai/codex-<os>-<cpu>` and each
    platform package declares the `os`/`cpu` it serves; neither side is retyped here, so a new
    architecture upstream picks up the right package with no change in this installer. npm may nest
    the optional package inside the root package or hoist it next to it, so both trees are searched.
    """
    os_name = CODEX_OS_NAMES.get(platform.system())
    cpu_name = CODEX_CPU_NAMES.get(platform.machine().lower())
    if os_name is None or cpu_name is None:
        raise InstallerError(
            f"Cannot derive the Codex platform package for {platform.system()}/{platform.machine()}: "
            "neither the Codex launcher nor this installer knows that platform"
        )
    searched = [package / "node_modules" / "@openai", package.parent]
    for directory in searched:
        for candidate in sorted(directory.glob("codex-*")):
            metadata = candidate / "package.json"
            if not metadata.is_file():
                continue
            try:
                declared = json.loads(metadata.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise InstallerError(f"Cannot read {metadata}: {exc}") from exc
            if os_name in declared.get("os", []) and cpu_name in declared.get("cpu", []):
                return candidate
    raise InstallerError(
        f"Codex installed no platform package for {os_name}/{cpu_name} under "
        f"{' or '.join(str(directory) for directory in searched)} "
        "(npm optionals were probably omitted); Codex would fail at run time with "
        "'Missing optional dependency'"
    )


def codex_vendor_layout(platform_package_dir: Path) -> tuple[Path, dict]:
    """The vendor directory the platform package ships its native payload in, plus its manifest."""
    vendor_dirs = sorted(path for path in (platform_package_dir / "vendor").glob("*") if path.is_dir())
    usables = [path for path in vendor_dirs if (path / "codex-package.json").is_file()]
    if len(usables) != 1:
        raise InstallerError(
            f"Codex platform package {platform_package_dir} must ship exactly one vendor directory with a "
            f"codex-package.json; found {[path.name for path in vendor_dirs] or 'none'}"
        )
    vendor_dir = usables[0]
    try:
        manifest = json.loads((vendor_dir / "codex-package.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise InstallerError(f"Cannot read {vendor_dir / 'codex-package.json'}: {exc}") from exc
    if manifest.get("layoutVersion") != 1:
        raise InstallerError(
            f"{vendor_dir / 'codex-package.json'} declares layoutVersion {manifest.get('layoutVersion')!r}; "
            "this installer understands layoutVersion 1"
        )
    return vendor_dir, manifest


def require_executable_binary(path: Path, description: str) -> None:
    """A regular file with an executable bit; a directory or a data file would not run."""
    if not path.is_file():
        raise InstallerError(f"Missing {description}: {path}")
    if not path.stat().st_mode & 0o111:
        raise InstallerError(f"{description} is not executable: {path}")


def verify_codex_install(prefix: Path, version: str) -> None:
    """Verify the npm-installed Codex tree in place; nothing is copied out of it.

    `codex` is a Node launcher that execve()s the native binary in the platform package's vendor
    `bin/`, and code mode spawns `codex-code-mode-host` from the same directory. `codex update`
    rewrites this whole tree (`npm install -g @openai/codex`), so a companion copied elsewhere would
    silently drift; the check is here, on the tree itself.
    """
    platform_package = prefix / "lib" / "node_modules" / "@openai" / "codex"
    if not platform_package.is_dir():
        raise InstallerError(f"Missing the installed Codex package: {platform_package}")
    try:
        installed = json.loads((platform_package / "package.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise InstallerError(f"Cannot read {platform_package / 'package.json'}: {exc}") from exc
    if installed.get("version") != version:
        raise InstallerError(
            f"Codex installed version {installed.get('version')!r}, but CODEX_VER pins {version!r}"
        )

    platform_name = codex_platform_package_dir(platform_package)
    vendor_dir, manifest = codex_vendor_layout(platform_name)
    if manifest.get("version") != version:
        raise InstallerError(
            f"{vendor_dir / 'codex-package.json'} records version {manifest.get('version')!r}, "
            f"but CODEX_VER pins {version!r}"
        )
    if manifest.get("target") != vendor_dir.name:
        raise InstallerError(
            f"{vendor_dir / 'codex-package.json'} names target {manifest.get('target')!r}, "
            f"but its directory is {vendor_dir.name!r}"
        )
    require_executable_binary(vendor_dir / "bin" / "codex", "Codex native binary")
    require_executable_binary(
        vendor_dir / "bin" / "codex-code-mode-host", "Codex code-mode host companion"
    )


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
    version = staged_version("CLAUDE_CODE_VER", "Claude Code")
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

    version = staged_version("REASONIX_VER", "Reasonix")
    require_command("npm", "npm is required to install Reasonix")
    run_command(["npm", "install", "-g", f"reasonix@{version}"])


def install_openclaw() -> None:
    if not is_enabled("INSTALL_OPENCLAW", True):
        log("INFO", "INSTALL_OPENCLAW=false; skipping OpenClaw")
        return

    version = staged_version("OPENCLAW_VER", "OpenClaw")
    require_command("npm", "npm is required to install OpenClaw")
    before = staged_before("OPENCLAW_BEFORE", "OpenClaw")
    run_command(["npm", "install", "-g", "--before", before, f"openclaw@{version}"])


def install_copilot() -> None:
    if not is_enabled("INSTALL_COPILOT", True):
        log("INFO", "INSTALL_COPILOT=false; skipping GitHub Copilot CLI")
        return

    version = staged_version("COPILOT_VER", "GitHub Copilot CLI")
    require_command("npm", "npm is required to install GitHub Copilot CLI")
    before = staged_before("COPILOT_BEFORE", "GitHub Copilot CLI")
    run_command(["npm", "install", "-g", "--before", before, f"@github/copilot@{version}"])


def install_claudelink() -> None:
    if not is_enabled("INSTALL_CLAUDELINK", True):
        log("INFO", "INSTALL_CLAUDELINK=false; skipping ClaudeLink")
        return

    version = staged_version("CLAUDELINK_VER", "ClaudeLink")
    require_command("npm", "npm is required to install ClaudeLink")
    before = staged_before("CLAUDELINK_BEFORE", "ClaudeLink")
    run_command(["npm", "install", "-g", "--before", before, f"claudelink@{version}"])


def install_pi() -> None:
    if not is_enabled("INSTALL_PI", True):
        log("INFO", "INSTALL_PI=false; skipping Pi")
        return

    version = staged_version("PI_VER", "Pi")
    require_command("npm", "npm is required to install Pi")
    run_command(["npm", "install", "-g", f"@earendil-works/pi-coding-agent@{version}"])


def install_aider(ctx: InstallerContext) -> None:
    if not is_enabled("INSTALL_AIDER", True):
        log("INFO", "INSTALL_AIDER=false; skipping Aider")
        return

    # The documented exception (aider-main-pin.toml): an exact COMMIT of upstream main, never `@main`.
    version = staged_version("AIDER_VER", "Aider")
    commit = env_value("AIDER_COMMIT")
    if not COMMIT_SHAPE.fullmatch(commit):
        raise InstallerError(
            f"AIDER_COMMIT must be a full 40-hex commit sha staged from aider-main-pin.toml (got {commit!r}); "
            "refusing to install an unpinned aider main"
        )
    before = staged_before("AIDER_BEFORE", "Aider")
    spec = f"aider-chat @ git+https://github.com/Aider-AI/aider.git@{commit}"

    run_command(
        [
            str(ctx.venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            "--uploaded-prior-to",
            before,
            spec,
        ]
    )
    installed = installed_pip_version(ctx.venv_python, "aider-chat")
    if installed != version:
        raise InstallerError(
            f"aider commit {commit} built version {installed!r}, but aider-main-pin.toml records {version!r}; "
            "update the recorded version (the pin file is the record the image manifest shows)"
        )


def installed_pip_version(python: Path, package: str) -> str:
    proc = subprocess.run([str(python), "-m", "pip", "show", package], capture_output=True, text=True, check=False)
    for line in proc.stdout.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    raise InstallerError(f"cannot read the installed version of {package}: {proc.stderr.strip()[:200]}")


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

    version = staged_version("OPENCODE_VER", "OpenCode")
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
