#!/usr/bin/env python3
"""Shared manifest rendering helpers for image and repo-hosted docs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence


PACKAGE_LIST_FILE = Path(__file__).resolve().parent.parent / "apt" / "packages.list"

FIRST_PARTY_WHEEL_NAMES = ("CIU", "cmru")
AI_CLI_TOOL_NAMES = ("aider", "antigravity", "claude", "codex", "copilot", "openclaw", "reasonix")
CONTAINER_INSPECTION_TOOL_NAMES = ("dive", "dtop", "glances", "lazydocker", "syft")
SECURITY_DEBUG_TOOL_NAMES = ("cdebug", "grype", "hadolint")
CUSTOM_TOOL_EXCLUSIONS = (
    FIRST_PARTY_WHEEL_NAMES
    + AI_CLI_TOOL_NAMES
    + CONTAINER_INSPECTION_TOOL_NAMES
    + SECURITY_DEBUG_TOOL_NAMES
    + ("psql", "redis-cli")
)

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
    "reasonix": "https://github.com/reasonix/reasonix",
    "awscli": "https://github.com/aws/aws-cli",
    "b2": "https://github.com/Backblaze/B2_Command_Line_Tool",
    "bat": "https://github.com/sharkdp/bat",
    "consul": "https://github.com/hashicorp/consul",
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
    """Render a tool table with columns: Tool, Version, Policy, Project Home, Package digest.

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
        "| Tool | Version | Policy | Project Home | Package digest |",
        "|---|---|---|---|---|",
    ]
    for name, version in items:
        policy = TOOL_POLICIES.get(name, "latest")
        project_home = PROJECT_HOMES.get(name, "")
        digest = ""
        if artifact_map and name in artifact_map:
            dig = artifact_map[name].get("sha256", "")
            if dig:
                digest = f"`sha256:{dig[:24]}…`"
        version_cell = f"`{version}`"
        name_cell = f"`{name}`" if name else "-"
        line = f"| {name_cell} | {version_cell} | {policy} | {project_home} | {digest} |"
        lines.append(line)
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
) -> str:
    """Render the unified devcontainer manifest (in-image + repo-hosted).

    Structure:
    1. Release / Pull / Base  (from source or generated)
    2. Version Selection Policy (generated preamble)
    3. AI CLI Tools           (table, live-inspected)
    4. Container Inspection Tools (table, live-inspected)
    5. Security & Debug Tools (table, live-inspected)
    6. Custom Tooling         (table, live-inspected)
    7. Python Packages        (pip freeze inside image)
    8. System Packages        (dpkg-query inside image)
    9. Rich Documentation Links / Notes (from source manifest)
    """
    tag = f"{debian_version}-py{python_version}-{image_version}"
    src = parse_source_manifest_sections(source_manifest_content) if source_manifest_content else {}

    lines: list[str] = [f"# Devcontainer Manifest — {tag}", ""]

    if "Release" in src:
        lines.extend(_section_block("Release", src["Release"]))
    else:
        lines.extend([
            "## Release", "",
            f"- Image tag: `{tag}`",
            f"- Debian: `{debian_version}`",
            f"- Python: `{python_version}`",
            f"- Image version: `{image_version}`",
            "",
        ])

    if "Pull" in src:
        lines.extend(_section_block("Pull", src["Pull"]))

    if "Base" in src:
        lines.extend(_section_block("Base", src["Base"]))
    else:
        lines.extend([
            "## Base", "",
            f"- Debian: {debian_version}",
            f"- Python: {python_version}",
            f"- Image version: {image_version}",
            f"- Image tag: {tag}",
            f"- Devcontainers release: {devcontainers_release.strip() or 'unknown'}",
            f"- Devcontainers image version: {devcontainers_version.strip() or 'unknown'}",
            "",
        ])

    # First-Party Wheels — from source manifest if present, otherwise live.
    if "First-Party Wheels" in src:
        lines.extend(_section_block("First-Party Wheels", src["First-Party Wheels"]))
    else:
        lines.extend(["## First-Party Wheels", ""])
        lines.extend(
            render_tool_table(custom_tooling, include=FIRST_PARTY_WHEEL_NAMES, artifact_map=artifact_map)
        )

    # AI CLI Tools — table with policy note.
    lines.extend(["## AI CLI Tools", ""])
    an = POLICY_NOTES.get("AI CLI Tools", "")
    if an:
        lines.append(an)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, include=AI_CLI_TOOL_NAMES, artifact_map=artifact_map)
    )

    # Container Inspection Tools — table with policy note.
    lines.extend(["## Container Inspection Tools", ""])
    cin = POLICY_NOTES.get("Container Inspection Tools", "")
    if cin:
        lines.append(cin)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, include=CONTAINER_INSPECTION_TOOL_NAMES, artifact_map=artifact_map)
    )

    # Security & Debug Tools — table with policy note.
    lines.extend(["## Security & Debug Tools", ""])
    sn = POLICY_NOTES.get("Security & Debug Tools", "")
    if sn:
        lines.append(sn)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, include=SECURITY_DEBUG_TOOL_NAMES, artifact_map=artifact_map)
    )

    # Custom Tooling — table with policy note.
    lines.extend(["## Custom Tooling", ""])
    ctn = POLICY_NOTES.get("Custom Tooling", "")
    if ctn:
        lines.append(ctn)
        lines.append("")
    lines.extend(
        render_tool_table(custom_tooling, exclude=CUSTOM_TOOL_EXCLUSIONS, artifact_map=artifact_map)
    )

    # Python Packages (pip freeze inside the image — the actual installed closure).
    lines.extend(["## Python Packages", "", "    (installed via pip)"])
    ppn = POLICY_NOTES.get("Python Packages", "")
    if ppn:
        lines.append(ppn)
        lines.append("")
    if python_packages:
        lines.extend([f"    {item}" for item in python_packages])
    else:
        lines.append("    unavailable")
    lines.append("")

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

    # Pass through any remaining sections from the source manifest that are not
    # explicitly handled above (e.g. "Python & PHP Runtime", "Version Selection
    # Policies", "In-Image File", "Notes", "Rich Documentation Links").
    # List of section titles that are already generated or handled explicitly.
    _HANDLED_SECTIONS = {
        "Release", "Pull", "Base",
        "First-Party Wheels",
        "AI CLI Tools", "Container Inspection Tools",
        "Security & Debug Tools", "Custom Tooling",
        "Python Packages", "System Packages",
        "Rich Documentation Links", "Notes",
        "Runtime Version Snapshot (Pre-build Probe)", "Runtime Version Snapshot",
        "Staged Tool Artifacts", "Appendix: Artifact Sources and Digests",
    }
    for key, content in src.items():
        if key == "__preamble__":
            continue
        if key in _HANDLED_SECTIONS:
            continue
        lines.extend(_section_block(key, content))

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
