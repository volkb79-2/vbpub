#!/usr/bin/env python3
"""Shared manifest rendering helpers for image and repo-hosted docs.

Single source of truth for all manifest generation. Called both at Docker build
time (inside the image) and post-build to extract the same manifest into
``package-manifests-versioned/`` so the committed and in-image versions are
always identical.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence


PACKAGE_LIST_FILE = Path(__file__).resolve().parent.parent / "apt" / "packages.list"

FIRST_PARTY_WHEEL_NAMES = ("CIU", "cmru")
AI_CLI_TOOL_NAMES = ("aider", "antigravity", "claude", "codex", "copilot", "openclaw", "opencode", "reasonix")
CONTAINER_INSPECTION_TOOL_NAMES = (
    "crane",
    "dive",
    "dtop",
    "glances",
    "lazydocker",
    "regctl",
    "syft",
)
SECURITY_DEBUG_TOOL_NAMES = ("cdebug", "grype", "hadolint")
# Node and npm are rendered in their own ## Node Runtime section.
NODE_RUNTIME_NAMES = ("node", "npm")
CUSTOM_TOOL_EXCLUSIONS = (
    FIRST_PARTY_WHEEL_NAMES
    + AI_CLI_TOOL_NAMES
    + CONTAINER_INSPECTION_TOOL_NAMES
    + SECURITY_DEBUG_TOOL_NAMES
    + NODE_RUNTIME_NAMES
    + ("psql", "redis-cli")
)

# ── Installation-source annotations ───────────────────────────────────────
# Shown as a parenthetical in each section heading.  Keys match section titles.
INSTALLATION_SOURCES: dict[str, str] = {
    "Node Runtime": "source: nodesource apt repo (https://deb.nodesource.com)",
    "Python & PHP Runtime": "source: Debian trixie apt / sury.org PHP repo (https://packages.sury.org/php)",
    "First-Party Wheels": "source: built from source, installed via pip",
    "AI CLI Tools": "source: npm / PyPI / GitHub Releases",
    "Container Inspection Tools": "source: GitHub Releases (pre-built binaries)",
    "Security & Debug Tools": "source: GitHub Releases (pre-built binaries, sha256-verified)",
    "Custom Tooling": "source: GitHub Releases / Debian apt",
    "Python Packages": "source: PyPI (resolved at build time via pip)",
    "System Packages": "source: Debian trixie apt",
}

# ── Version selection policy ────────────────────────────────────────────────
# Each tool category declares how versions are selected.  This is displayed
# in a policy-note block at the top of each tool table section.
POLICY_NOTES = {
    "AI CLI Tools": (
        "**Version policy:** latest npm/GitHub release at build time (override via build arg). "
        "AI CLI tool versions are resolved dynamically during `stage_tool_artifacts` from "
        "the respective package registries (npm, PyPI, GitHub Releases)."
    ),
    "Container Inspection Tools": (
        "**Version policy:** latest GitHub release at build time (override via build arg). "
        "All tools in this category are downloaded as pre-built binaries from their upstream releases."
    ),
    "Security & Debug Tools": (
        "**Version policy:** latest GitHub release at build time (override via build arg). "
        "Binaries are verified via upstream SHA256 checksums before installation."
    ),
    "Custom Tooling": (
        "**Version policy:** latest GitHub release at build time (override via build arg). "
        "Some tools are compiled from source (nvim, htop); the rest are pre-built binaries."
    ),
    "System Packages": (
        "**Version policy:** Debian repository versions (prefer backports when available). "
        "System packages come from the Debian Trixie main repos; devcontainer-features "
        "are installed via the features CLI.  Exception: `skopeo` is pulled from "
        "Debian testing (pin-priority 501) for a newer version than trixie provides."
    ),
    "Python Packages": (
        "**Version policy:** PyPI latest at image build time (resolved via pip install). "
        "The primary venv contains full toolkit.txt closure; secondary venvs are lean "
        "(uv + debugpy + ruff only)."
    ),
}

# Mapping of installed binaries to their upstream project home URLs.
PROJECT_HOMES = {
    "aider": "https://github.com/Aider-AI/aider",
    "antigravity": "https://github.com/antigravity/antigravity-cli",
    "claude": "https://github.com/anthropics/claude-code",
    "codex": "https://github.com/openai/codex",
    "copilot": "https://github.com/github/copilot-cli",
    "openclaw": "https://github.com/openclaw/openclaw",
    "opencode": "https://github.com/anomalyco/opencode",
    "reasonix": "https://github.com/reasonix/reasonix",
    "awscli": "https://github.com/aws/aws-cli",
    "b2": "https://github.com/Backblaze/B2_Command_Line_Tool",
    "bat": "https://github.com/sharkdp/bat",
    "consul": "https://github.com/hashicorp/consul",
    "crane": "https://github.com/google/go-containerregistry",
    "cdebug": "https://github.com/iximiuz/cdebug",
    "delta": "https://github.com/dandavison/delta",
    "dive": "https://github.com/wagoodman/dive",
    "dtop": "https://github.com/amir20/dtop",
    "fd": "https://github.com/sharkdp/fd",
    "fzf": "https://github.com/junegunn/fzf",
    "gh": "https://github.com/cli/cli",
    "glances": "https://github.com/nicolargo/glances",
    "grpcurl": "https://github.com/fullstorydev/grpcurl",
    "grype": "https://github.com/anchore/grype",
    "hadolint": "https://github.com/hadolint/hadolint",
    "htop": "https://github.com/htop-dev/htop",
    "lazydocker": "https://github.com/jesseduffield/lazydocker",
    "nvchad": "https://github.com/NvChad/NvChad",
    "nvim": "https://github.com/neovim/neovim",
    "rga": "https://github.com/phiresky/ripgrep-all",
    "regctl": "https://github.com/regclient/regclient",
    "ripgrep": "https://github.com/BurntSushi/ripgrep",
    "shellcheck": "https://github.com/koalaman/shellcheck",
    "skopeo": "https://github.com/containers/skopeo",
    "syft": "https://github.com/anchore/syft",
    "vault": "https://github.com/hashicorp/vault",
    "yq": "https://github.com/mikefarah/yq",
}

# Policy labels per tool (appended to version cell).
TOOL_POLICIES: dict[str, str] = {}  # tool → policy label; falls back to "latest" or "apt"


def netcat_package_for_debian(debian_version: str) -> str:
    # Standardised on netcat-openbsd for all variants (available on bookworm + trixie).
    # The parameter is kept for call-site compatibility.
    return "netcat-openbsd"


def parse_key_value_lines(lines: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        parsed[key] = value.strip()
    return parsed


def read_key_value_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_key_value_lines(path.read_text(encoding="utf-8").splitlines())


def read_list_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_apt_package_names(path: Path = PACKAGE_LIST_FILE) -> list[str]:
    if not path.exists():
        return []
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.partition("#")[0].strip()
        if not line or line in seen:
            continue
        names.append(line)
        seen.add(line)
    return names


def _sorted_tool_items(tooling: Mapping[str, str]) -> list[tuple[str, str]]:
    return [
        (name, value.strip())
        for name, value in sorted(tooling.items(), key=lambda item: item[0].lower())
        if str(value).strip()
    ]


def render_named_lines(
    tooling: Mapping[str, str],
    *,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
    unavailable_text: str = "- unavailable",
) -> list[str]:
    excluded = set(exclude)
    included = set(include) if include is not None else None
    filtered = {
        key: value
        for key, value in tooling.items()
        if key not in excluded and (included is None or key in included)
    }
    items = _sorted_tool_items(filtered)
    if not items:
        return [unavailable_text]
    return [f"- {name}: `{value}`" for name, value in items]


def render_system_package_lines(system_packages: Sequence[str]) -> list[str]:
    parsed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_item in system_packages:
        item = str(raw_item).strip()
        if not item:
            continue
        name, sep, value = item.partition("=")
        if sep:
            label = name.strip()
            version = value.strip()
            if not label or not version:
                continue
            key = label.lower()
            if key in seen:
                continue
            parsed.append((label, version))
            seen.add(key)
            continue
        key = item.lower()
        if key in seen:
            continue
        parsed.append((item, ""))
        seen.add(key)

    items = sorted(parsed, key=lambda item: item[0].lower())
    if not items:
        return ["- unavailable"]
    rendered: list[str] = []
    for name, version in items:
        if version:
            rendered.append(f"- {name}: `{version}`")
        else:
            rendered.append(f"- {name}")
    return rendered


def render_tool_table(
    tools: Mapping[str, str],
    *,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
    artifact_map: Mapping[str, dict] | None = None,
) -> list[str]:
    """Render a tool table with version, policy, project, and immutable artifact evidence.

    Artifact metadata (digest, source URL) can be provided via artifact_map, keyed by tool name.
    """
    excluded = set(exclude)
    included = set(include) if include is not None else None
    filtered = {
        key: value
        for key, value in tools.items()
        if key not in excluded and (included is None or key in included)
    }
    items = _sorted_tool_items(filtered)
    if not items:
        return ["(none)", ""]

    lines = [
        "| Tool | Version | Policy | Project Home | Package digest / source |",
        "|---|---|---|---|---|",
    ]
    for name, version in items:
        policy = TOOL_POLICIES.get(name, "latest")
        project_home = PROJECT_HOMES.get(name, "")
        digest = ""
        if artifact_map and name in artifact_map:
            dig = artifact_map[name].get("sha256", "")
            source_url = artifact_map[name].get("source_url", "")
            if dig:
                digest = f"`sha256:{dig[:24]}…`"
            if source_url:
                digest = f"[{digest or 'source'}]({source_url})"
        version_cell = f"`{version}`"
        name_cell = f"`{name}`" if name else "-"
        line = f"| {name_cell} | {version_cell} | {policy} | {project_home} | {digest} |"
        lines.append(line)
    lines.append("")
    return lines


def _section_heading(title: str) -> str:
    """Return a ``## Section Title`` with optional source annotation."""
    source = INSTALLATION_SOURCES.get(title)
    if source:
        return f"## {title} ({source})"
    return f"## {title}"


def render_node_runtime_section(
    custom_tooling: Mapping[str, str],
) -> list[str]:
    """Render the Node Runtime section (separate from Custom Tooling)."""
    node_ver = custom_tooling.get("node", "")
    npm_ver = custom_tooling.get("npm", "")
    lines = [_section_heading("Node Runtime"), ""]
    if node_ver:
        lines.append(f"- Node.js: `{node_ver}`")
    else:
        lines.append("- Node.js: not installed")
    if npm_ver:
        lines.append(f"- npm: `{npm_ver}`")
    lines.append("")
    return lines


def render_php_runtime_section(
    custom_tooling: Mapping[str, str],
    *,
    php_extensions_file: str = "",
    install_php: bool = False,
) -> list[str]:
    """Render the PHP Runtime section when PHP is installed.

    Shows PHP version, composer version, source (sury.org), and the list of
    loaded PHP extensions from ``php -m`` output (captured at build time).
    """
    lines = [_section_heading("Python & PHP Runtime"), ""]
    if not install_php:
        return lines

    php_version = custom_tooling.get("php", "unknown")
    composer_version = custom_tooling.get("composer", "")
    lines.append(f"- PHP: `{php_version}`")
    if composer_version:
        lines.append(f"- Composer: `{composer_version}`")
    lines.append("")

    # Load php -m extension listing captured at build time.
    ext_lines: list[str] = []
    if php_extensions_file:
        ext_path = Path(php_extensions_file)
        if ext_path.exists():
            raw = ext_path.read_text(encoding="utf-8").strip()
            if raw:
                ext_lines = [line.strip() for line in raw.splitlines() if line.strip()]

    if ext_lines:
        lines.append("### PHP Extensions (loaded modules)")
        lines.append("")
        for ext in ext_lines:
            lines.append(f"- `{ext}`")
        lines.append("")
    return lines


def render_runtime_probe_sections(
    custom_tooling: Mapping[str, str],
    system_packages: Sequence[str],
    artifact_map: Mapping[str, dict] | None = None,
) -> list[str]:
    lines = [
        "## Runtime Version Snapshot (Pre-build Probe)",
        "",
    ]
    sections: list[tuple[str, Sequence[str]]] = [
        ("First-Party Wheels", FIRST_PARTY_WHEEL_NAMES),
        ("AI CLI Tools", AI_CLI_TOOL_NAMES),
        ("Container Inspection Tools", CONTAINER_INSPECTION_TOOL_NAMES),
        ("Security & Debug Tools", SECURITY_DEBUG_TOOL_NAMES),
    ]

    for title, names in sections:
        policy_note = POLICY_NOTES.get(title, "")
        lines.extend([f"### {title}", ""])
        if policy_note:
            lines.append(policy_note)
            lines.append("")
        lines.extend(
            render_tool_table(
                custom_tooling,
                include=names,
                artifact_map=artifact_map,
            )
        )

    # Custom tooling (everything not in the named categories)
    lines.extend(["### Custom Tooling", ""])
    cn = POLICY_NOTES.get("Custom Tooling", "")
    if cn:
        lines.append(cn)
        lines.append("")
    lines.extend(
        render_tool_table(
            custom_tooling,
            exclude=CUSTOM_TOOL_EXCLUSIONS,
            artifact_map=artifact_map,
        )
    )

    lines.extend(
        [
            "### System packages",
            "",
            "    (candidate versions from apt probe)",
        ]
    )
    if system_packages:
        lines.extend(render_system_package_lines(system_packages))
    else:
        lines.append("    probe-unavailable")
    lines.append("")
    return lines


def render_installed_manifest(
    *,
    debian_version: str,
    python_version: str,
    image_version: str,
    devcontainers_release: str,
    devcontainers_version: str,
    custom_tooling: Mapping[str, str],
    python_packages: Sequence[str],
    system_packages: Sequence[str],
    artifact_map: Mapping[str, dict] | None = None,
) -> str:
    lines = [
        "# Image Manifest",
        "",
        "## Base",
        f"- Debian: {debian_version}",
        f"- Python: {python_version}",
        f"- Image version: {image_version}",
        f"- Image tag: {debian_version}-py{python_version}-{image_version}",
    ]

    release_value = devcontainers_release.strip()
    version_value = devcontainers_version.strip()
    lines.append(
        f"- Devcontainers release: {release_value if release_value else 'unknown'}"
    )
    lines.append(
        f"- Devcontainers image version: {version_value if version_value else 'unknown'}"
    )

    # First-Party Wheels
    lines.extend(["", "## First-Party Wheels", ""])
    lines.extend(
        render_tool_table(
            custom_tooling,
            include=FIRST_PARTY_WHEEL_NAMES,
            artifact_map=artifact_map,
        )
    )

    # AI CLI Tools
    lines.extend(["## AI CLI Tools", ""])
    an = POLICY_NOTES.get("AI CLI Tools", "")
    if an:
        lines.append(an)
        lines.append("")
    lines.extend(
        render_tool_table(
            custom_tooling,
            include=AI_CLI_TOOL_NAMES,
            artifact_map=artifact_map,
        )
    )

    # Container Inspection Tools
    lines.extend(["## Container Inspection Tools", ""])
    cn = POLICY_NOTES.get("Container Inspection Tools", "")
    if cn:
        lines.append(cn)
        lines.append("")
    lines.extend(
        render_tool_table(
            custom_tooling,
            include=CONTAINER_INSPECTION_TOOL_NAMES,
            artifact_map=artifact_map,
        )
    )

    # Security & Debug Tools
    lines.extend(["## Security & Debug Tools", ""])
    sn = POLICY_NOTES.get("Security & Debug Tools", "")
    if sn:
        lines.append(sn)
        lines.append("")
    lines.extend(
        render_tool_table(
            custom_tooling,
            include=SECURITY_DEBUG_TOOL_NAMES,
            artifact_map=artifact_map,
        )
    )

    # Custom Tooling (everything else)
    lines.extend(["## Custom Tooling", ""])
    ctn = POLICY_NOTES.get("Custom Tooling", "")
    if ctn:
        lines.append(ctn)
        lines.append("")
    lines.extend(
        render_tool_table(
            custom_tooling,
            exclude=CUSTOM_TOOL_EXCLUSIONS,
            artifact_map=artifact_map,
        )
    )

    # Python packages
    lines.extend(["", "## Python Packages", "", "    (installed via pip)"])
    pn = POLICY_NOTES.get("Python Packages", "")
    if pn:
        lines.append(pn)
        lines.append("")
    if python_packages:
        lines.extend([f"    {item}" for item in python_packages])
    else:
        lines.append("    unavailable")
    lines.append("")

    # System packages
    lines.extend(["## System Packages", "", "    (installed via apt)"])
    spn = POLICY_NOTES.get("System Packages", "")
    if spn:
        lines.append(spn)
        lines.append("")
    if system_packages:
        lines.extend(render_system_package_lines(system_packages))
    else:
        lines.append("    unavailable")
    lines.append("")

    return "\n".join(lines)


def parse_source_manifest_sections(content: str) -> dict[str, str]:
    """Parse a markdown document into {h2_title: content} mapping.

    The title (h1) and any text before the first ## are stored under "__preamble__".
    Content for each section is stripped of leading/trailing whitespace.
    """
    sections: dict[str, str] = {}
    current_key = "__preamble__"
    current_lines: list[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    sections[current_key] = "\n".join(current_lines).strip()
    return sections


def _section_block(title: str, content: str) -> list[str]:
    """Return lines for a ## section with normalized blank-line framing."""
    lines = [f"## {title}", ""]
    stripped = content.strip()
    if stripped:
        lines.extend(stripped.splitlines())
    lines.append("")
    return lines


def render_unified_manifest(
    *,
    source_manifest_content: str,
    debian_version: str,
    python_version: str,
    image_version: str,
    devcontainers_release: str,
    devcontainers_version: str,
    custom_tooling: Mapping[str, str],
    python_packages: Sequence[str],
    system_packages: Sequence[str],
    artifact_map: Mapping[str, dict] | None = None,
    args_extra: dict[str, str] | None = None,
) -> str:
    """Render the unified devcontainer manifest (in-image + repo-hosted).

    This is the SINGLE source of truth for manifest rendering. Called both at
    Docker build time (inside the image) and post-build to extract the same
    content into ``package-manifests-versioned/``.

    When ``source_manifest_content`` is provided, sections are passed through
    from it. Otherwise, sections are generated from the CLI arguments.
    ``args_extra`` carries optional metadata (description, target, variant,
    package_name, username, repo, install_php, php_extensions_file) for
    standalone use without a source manifest.

    Structure:
    1. Release / Pull / Base / Purpose
    2. Node Runtime
    3. Python & PHP Runtime
    4. First-Party Wheels
    5. AI CLI Tools
    6. Container Inspection Tools
    7. Security & Debug Tools
    8. Custom Tooling
    9. Python Packages
    10. System Packages
    11. Rich Documentation Links / Notes
    """
    variant = (args_extra or {}).get("variant", "").strip()
    variant_part = f"-{variant}" if variant else ""
    tag = f"{debian_version}-py{python_version}{variant_part}-{image_version}"
    floating_tag = f"{debian_version}-py{python_version}{variant_part}-latest"
    src = parse_source_manifest_sections(source_manifest_content) if source_manifest_content else {}

    package_name = (args_extra or {}).get("package_name", "")
    manifest_kind = "Devcontainer Manifest" if "vsc-devcontainer" in package_name else "Image Manifest"
    lines: list[str] = [f"# {manifest_kind} — {tag}", ""]

    if "Release" in src:
        lines.extend(_section_block("Release", src["Release"]))
    else:
        release_lines = [
            "## Release", "",
            f"- Build date: `{image_version}`",
        ]
        if args_extra and args_extra.get("target"):
            release_lines.append(f"- Target: `{args_extra['target']}`")
        release_lines.extend([
            f"- Debian: `{debian_version}`",
            f"- Python: `{python_version}`",
            f"- Immutable image tag: `{tag}`",
            f"- Floating image tag: `{floating_tag}`",
            "",
        ])
        lines.extend(release_lines)

    if "Pull" in src:
        lines.extend(_section_block("Pull", src["Pull"]))
    elif args_extra and args_extra.get("package_name") and args_extra.get("username") and args_extra.get("repo"):
        image_ref = (
            f"ghcr.io/{args_extra['username']}/{args_extra['package_name']}:{tag}"
        )
        lines.extend([
            "## Pull", "",
            "```bash",
            f"docker pull {image_ref}",
            "```",
            "",
        ])

    if "Base" in src:
        lines.extend(_section_block("Base", src["Base"]))
    else:
        base_lines = [
            "## Base", "",
            f"- Debian: {debian_version}",
            f"- Python: {python_version}",
            f"- Image version: {image_version}",
            f"- Image tag: {tag}",
        ]
        if devcontainers_release.strip():
            base_lines.append(f"- Devcontainers release: {devcontainers_release.strip()}")
        if devcontainers_version.strip():
            base_lines.append(f"- Devcontainers image version: {devcontainers_version.strip()}")
        base_lines.append("")
        lines.extend(base_lines)

    # Purpose section — from source manifest or description arg.
    if "Purpose" in src:
        lines.extend(_section_block("Purpose", src["Purpose"]))
    elif args_extra and args_extra.get("description"):
        lines.extend(["## Purpose", ""])
        lines.extend(args_extra["description"].strip().splitlines())
        lines.append("")

    # Node Runtime — separate from Custom Tooling.
    lines.extend(render_node_runtime_section(custom_tooling))

    # PHP Runtime — only when PHP is installed.
    if args_extra and args_extra.get("install_php"):
        lines.extend(
            render_php_runtime_section(
                custom_tooling,
                php_extensions_file=args_extra.get("php_extensions_file", ""),
                install_php=True,
            )
        )

    # First-Party Wheels — from source manifest if present, otherwise live.
    if "First-Party Wheels" in src:
        lines.extend(_section_block("First-Party Wheels", src["First-Party Wheels"]))
    else:
        lines.extend([_section_heading("First-Party Wheels"), ""])
        lines.extend(
            render_tool_table(custom_tooling, include=FIRST_PARTY_WHEEL_NAMES, artifact_map=artifact_map)
        )

    # AI CLI Tools — table with policy note.
    lines.extend([_section_heading("AI CLI Tools"), ""])
    an = POLICY_NOTES.get("AI CLI Tools", "")
    if an:
        lines.append(an)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, include=AI_CLI_TOOL_NAMES, artifact_map=artifact_map)
    )

    # Container Inspection Tools — table with policy note.
    lines.extend([_section_heading("Container Inspection Tools"), ""])
    cin = POLICY_NOTES.get("Container Inspection Tools", "")
    if cin:
        lines.append(cin)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, include=CONTAINER_INSPECTION_TOOL_NAMES, artifact_map=artifact_map)
    )

    # Security & Debug Tools — table with policy note.
    lines.extend([_section_heading("Security & Debug Tools"), ""])
    sn = POLICY_NOTES.get("Security & Debug Tools", "")
    if sn:
        lines.append(sn)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, include=SECURITY_DEBUG_TOOL_NAMES, artifact_map=artifact_map)
    )

    # Custom Tooling — table with policy note.
    lines.extend([_section_heading("Custom Tooling"), ""])
    ctn = POLICY_NOTES.get("Custom Tooling", "")
    if ctn:
        lines.append(ctn)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, exclude=CUSTOM_TOOL_EXCLUSIONS, artifact_map=artifact_map)
    )

    # Python Packages (pip freeze inside the image — the actual installed closure).
    lines.extend([_section_heading("Python Packages"), "", "    (installed via pip)"])
    ppn = POLICY_NOTES.get("Python Packages", "")
    if ppn:
        lines.append(ppn)
        lines.append("")
    if python_packages:
        lines.extend([f"    {item}" for item in python_packages])
    else:
        lines.append("    unavailable")
    lines.append("")

    lines.extend([_section_heading("System Packages"), "", "    (installed via apt)"])
    spn = POLICY_NOTES.get("System Packages", "")
    if spn:
        lines.append(spn)
        lines.append("")
    if system_packages:
        lines.extend(render_system_package_lines(system_packages))
    else:
        lines.append("    unavailable")
    lines.append("")

    # Pass through any remaining sections from the source manifest that are not
    # explicitly handled above.
    _HANDLED_SECTIONS = {
        "Release", "Pull", "Base", "Purpose",
        "First-Party Wheels",
        "AI CLI Tools", "Container Inspection Tools",
        "Security & Debug Tools", "Custom Tooling",
        "Python Packages", "System Packages",
        "Node Runtime", "Python & PHP Runtime",
        "Rich Documentation Links", "Notes",
        "Runtime Version Snapshot (Pre-build Probe)", "Runtime Version Snapshot",
        "Staged Tool Artifacts", "Appendix: Artifact Sources and Digests",
    }

    # If we generated a Purpose section from args_extra, also suppress it from pass-through.
    if args_extra and args_extra.get("description"):
        _HANDLED_SECTIONS.add("Purpose")

    # If we rendered the Node Runtime section, suppress legacy node info.
    _HANDLED_SECTIONS.add("Node Runtime")

    for key, content in src.items():
        if key == "__preamble__":
            continue
        if key in _HANDLED_SECTIONS:
            continue
        lines.extend(_section_block(key, content))

    # Rich Documentation Links — always append when we have the metadata.
    if "Rich Documentation Links" not in src and args_extra:
        _user = args_extra.get("username", "")
        _repo = args_extra.get("repo", "")
        _pkg = args_extra.get("package_name", "")
        _family_readme_url = (
            f"https://github.com/{_user}/{_repo}/blob/main/"
            f"modern-debian-tools-python-debug/package-manifests-versioned/"
            f"{_pkg}/README.md"
        )
        _release_url = (
            f"https://github.com/{_user}/{_repo}/blob/main/"
            f"modern-debian-tools-python-debug/package-manifests-versioned/"
            f"{_pkg}/{tag}.md"
        )
        _source_url = (
            f"https://github.com/{_user}/{_repo}/tree/main/"
            f"modern-debian-tools-python-debug"
        )
        lines.extend([
            "## Rich Documentation Links", "",
            f"- Family overview: {_family_readme_url}",
            f"- This release page: {_release_url}",
            f"- Source tree: {_source_url}",
            "",
        ])

    # In-Image File path.
    if "In-Image File" not in src:
        lines.extend([
            "## In-Image File", "",
            "- Image manifest: "
            "`/usr/local/share/modern-debian-tools-python-debug/manifest.md`",
            "",
        ])

    # Notes — only if not already in src.
    if "Notes" not in src:
        lines.extend([
            "## Notes", "",
            "This repository-hosted page exists because GHCR package descriptions "
            "render as flattened plain text.",
            "The image labels therefore point to GitHub-hosted Markdown for richer, "
            "package-specific release notes.",
            "The same manifest content is installed in-image at "
            "`/usr/local/share/modern-debian-tools-python-debug/manifest.md`.",
            "",
        ])

    return "\n".join(lines)


def _load_artifact_map(artifact_metadata_path: str | None) -> dict[str, dict]:
    """Load artifact metadata.json into a {tool_name: {sha256, source_url}} map.

    Only the primary artifact for each tool is kept (kind=tar.gz|binary|zip|deb);
    checksum-file and manifest entries are skipped.
    """
    if not artifact_metadata_path:
        return {}
    try:
        data = json.loads(Path(artifact_metadata_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    art_map: dict[str, dict] = {}
    primary_kinds = {"tar.gz", "tar.xz", "binary", "zip", "deb", "source-tarball", "pypi-network"}
    for entry in data.get("artifacts", []):
        if entry.get("kind") not in primary_kinds:
            continue
        tool = entry["tool"]
        # Skip dash-suffixed entries like "claude-manifest", "codex-sha256sums"
        if "-" in tool:
            continue
        if tool not in art_map:
            art_map[tool] = {
                "sha256": entry.get("sha256", ""),
                "source_url": entry.get("source_url", ""),
            }
    return art_map


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render manifest markdown sections")
    subparsers = parser.add_subparsers(dest="command", required=True)

    installed = subparsers.add_parser("installed", help="Render full installed-tools manifest")
    installed.add_argument("--output", required=True, help="Output markdown file path")
    installed.add_argument("--debian-version", required=True)
    installed.add_argument("--python-version", required=True)
    installed.add_argument("--image-version", required=True)
    installed.add_argument("--devcontainers-release", default="")
    installed.add_argument("--devcontainers-version", default="")
    installed.add_argument("--custom-tooling-file", required=True)
    installed.add_argument("--python-packages-file", required=True)
    installed.add_argument("--system-packages-file", required=True)
    installed.add_argument("--artifact-metadata", default="", help="Path to artifact metadata.json for digests")

    unified = subparsers.add_parser("unified", help="Render unified devcontainer manifest")
    unified.add_argument("--output", required=True, help="Output markdown file path")
    unified.add_argument("--source-manifest", default="", help="Path to repo-hosted source manifest (may be empty)")
    unified.add_argument("--debian-version", required=True)
    unified.add_argument("--python-version", required=True)
    unified.add_argument("--image-version", required=True)
    unified.add_argument("--devcontainers-release", default="")
    unified.add_argument("--devcontainers-version", default="")
    unified.add_argument("--custom-tooling-file", required=True)
    unified.add_argument("--python-packages-file", required=True)
    unified.add_argument("--system-packages-file", required=True)
    unified.add_argument("--artifact-metadata", default="", help="Path to artifact metadata.json for digests")
    # New args for standalone manifest generation (no source manifest needed).
    unified.add_argument("--description", default="", help="OCI description text (replaces Purpose section)")
    unified.add_argument("--target", default="", help="Bake target name (e.g. trixie-py314-vsc)")
    unified.add_argument("--variant", default="", help="Tag variant (e.g. php8.5)")
    unified.add_argument("--package-name", default="", help="GHCR package name")
    unified.add_argument("--username", default="", help="GitHub username/org")
    unified.add_argument("--repo", default="", help="GitHub repository name")
    unified.add_argument("--install-php", default="false", help="Whether PHP is installed ('true'/'false')")
    unified.add_argument("--php-extensions-file", default="", help="Path to php -m output file")

    return parser.parse_args()


def _run_installed(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    custom_tooling = read_key_value_file(Path(args.custom_tooling_file))
    python_packages = read_list_file(Path(args.python_packages_file))
    system_packages = read_list_file(Path(args.system_packages_file))
    artifact_map = _load_artifact_map(args.artifact_metadata)

    rendered = render_installed_manifest(
        debian_version=args.debian_version,
        python_version=args.python_version,
        image_version=args.image_version,
        devcontainers_release=args.devcontainers_release,
        devcontainers_version=args.devcontainers_version,
        custom_tooling=custom_tooling,
        python_packages=python_packages,
        system_packages=system_packages,
        artifact_map=artifact_map,
    )

    output_path.write_text(rendered, encoding="utf-8")
    return 0


def _run_unified(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    source_path = Path(args.source_manifest) if args.source_manifest else None
    source_content = ""
    if source_path and source_path.exists():
        source_content = source_path.read_text(encoding="utf-8")

    custom_tooling = read_key_value_file(Path(args.custom_tooling_file))
    python_packages = read_list_file(Path(args.python_packages_file))
    system_packages = read_list_file(Path(args.system_packages_file))
    artifact_map = _load_artifact_map(args.artifact_metadata)

    # Build args_extra from CLI args for standalone generation.
    args_extra: dict[str, str] = {}
    if args.description:
        args_extra["description"] = args.description
    if args.target:
        args_extra["target"] = args.target
    if args.variant:
        args_extra["variant"] = args.variant
    if args.package_name:
        args_extra["package_name"] = args.package_name
    if args.username:
        args_extra["username"] = args.username
    if args.repo:
        args_extra["repo"] = args.repo
    args_extra["install_php"] = (args.install_php or "false").lower() == "true"
    if args.php_extensions_file:
        args_extra["php_extensions_file"] = args.php_extensions_file

    rendered = render_unified_manifest(
        source_manifest_content=source_content,
        debian_version=args.debian_version,
        python_version=args.python_version,
        image_version=args.image_version,
        devcontainers_release=args.devcontainers_release,
        devcontainers_version=args.devcontainers_version,
        custom_tooling=custom_tooling,
        python_packages=python_packages,
        system_packages=system_packages,
        artifact_map=artifact_map,
        args_extra=args_extra,
    )

    output_path.write_text(rendered, encoding="utf-8")
    return 0


def main() -> int:
    args = _parse_args()
    if args.command == "installed":
        return _run_installed(args)
    if args.command == "unified":
        return _run_unified(args)
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
