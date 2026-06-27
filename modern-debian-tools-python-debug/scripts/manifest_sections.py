#!/usr/bin/env python3
"""Shared manifest rendering helpers for image and repo-hosted docs."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Mapping, Sequence


PACKAGE_LIST_FILE = Path(__file__).resolve().parent.parent / "apt" / "packages.list"

FIRST_PARTY_WHEEL_NAMES = ("CIU", "cmru")
AI_CLI_TOOL_NAMES = ("aider", "antigravity", "claude", "codex", "openclaw", "reasonix")
CONTAINER_INSPECTION_TOOL_NAMES = ("dive", "dtop", "glances", "lazydocker", "syft")
SECURITY_DEBUG_TOOL_NAMES = ("cdebug", "grype", "hadolint")
CUSTOM_TOOL_EXCLUSIONS = (
    FIRST_PARTY_WHEEL_NAMES
    + AI_CLI_TOOL_NAMES
    + CONTAINER_INSPECTION_TOOL_NAMES
    + SECURITY_DEBUG_TOOL_NAMES
    + ("psql", "redis-cli")
)


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


def render_runtime_probe_sections(
    custom_tooling: Mapping[str, str],
    system_packages: Sequence[str],
) -> list[str]:
    lines = [
        "## Runtime Version Snapshot (Pre-build Probe)",
        "",
    ]
    sections: list[tuple[str, list[str]]] = [
        ("First-Party Wheels", render_named_lines(custom_tooling, include=FIRST_PARTY_WHEEL_NAMES)),
        ("AI CLI Tools", render_named_lines(custom_tooling, include=AI_CLI_TOOL_NAMES)),
        (
            "Container Inspection Tools",
            render_named_lines(custom_tooling, include=CONTAINER_INSPECTION_TOOL_NAMES),
        ),
        ("Security & Debug Tools", render_named_lines(custom_tooling, include=SECURITY_DEBUG_TOOL_NAMES)),
        (
            "Custom Tooling",
            render_named_lines(
                custom_tooling,
                exclude=CUSTOM_TOOL_EXCLUSIONS,
            ),
        ),
    ]

    for title, section_lines in sections:
        lines.extend([f"### {title}", ""])
        lines.extend(section_lines)
        lines.append("")

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

    lines.extend(
        [
            "",
            "## First-Party Wheels",
            "",
        ]
    )
    lines.extend(render_named_lines(custom_tooling, include=FIRST_PARTY_WHEEL_NAMES))
    lines.extend(
        [
            "",
            "## AI CLI Tools",
            "",
        ]
    )
    lines.extend(
        render_named_lines(
            custom_tooling,
            include=AI_CLI_TOOL_NAMES,
        )
    )
    lines.extend(
        [
            "",
            "## Container Inspection Tools",
            "",
        ]
    )
    lines.extend(render_named_lines(custom_tooling, include=CONTAINER_INSPECTION_TOOL_NAMES))
    lines.extend(
        [
            "",
            "## Security & Debug Tools",
            "",
        ]
    )
    lines.extend(render_named_lines(custom_tooling, include=SECURITY_DEBUG_TOOL_NAMES))
    lines.extend(
        [
            "",
            "## Custom Tooling",
            "",
        ]
    )
    lines.extend(
        render_named_lines(
            custom_tooling,
            exclude=CUSTOM_TOOL_EXCLUSIONS,
        )
    )

    lines.extend(
        [
            "",
            "## Python packages",
            "",
            "    (installed via pip)",
        ]
    )
    if python_packages:
        lines.extend([f"    {item}" for item in python_packages])
    else:
        lines.append("    unavailable")

    lines.extend(
        [
            "",
            "## System packages",
            "",
            "    (installed via apt)",
        ]
    )
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
) -> str:
    """Render the unified devcontainer manifest (in-image + repo-hosted).

    Structure:
    1. Release / Pull / Base  (from source or generated)
    2. First-Party Wheels     (live-inspected at image build time)
    3. AI CLI Tools           (live-inspected at image build time)
    4. Container Inspection Tools (live-inspected at image build time)
    5. Custom Tooling         (live-inspected at image build time)
    6. Python Packages        (pip freeze inside image)
    7. System Packages        (dpkg-query inside image)
    8. Runtime Version Snapshot (pre-build probe from source manifest)
    9. Rich Documentation Links / Notes (from source manifest)
    10. Appendix: Artifact Sources and Digests  (moved to end for readability)
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

    release_val = devcontainers_release.strip() or "unknown"
    version_val = devcontainers_version.strip() or "unknown"
    lines.extend([
        "## Base", "",
        f"- Debian: {debian_version}",
        f"- Python: {python_version}",
        f"- Image version: {image_version}",
        f"- Image tag: {tag}",
        f"- Devcontainers release: {release_val}",
        f"- Devcontainers image version: {version_val}",
        "",
    ])

    lines.extend(["## Custom Tooling", ""])
    lines.extend(
        render_named_lines(
            custom_tooling,
            exclude=CUSTOM_TOOL_EXCLUSIONS,
        )
    )
    lines.append("")

    # First-Party Wheels — carry through from the repo-hosted source manifest.
    if "First-Party Wheels" in src:
        lines.extend(_section_block("First-Party Wheels", src["First-Party Wheels"]))

    # Staged Tool Artifacts — resolved versions only (digests go to appendix).
    if "Staged Tool Artifacts" in src:
        lines.extend(_section_block("Staged Tool Artifacts", src["Staged Tool Artifacts"]))

    # Python Packages (pip freeze inside the image — the actual installed closure).
    lines.extend(["## Python Packages", "", "    (installed via pip)"])
    if python_packages:
        lines.extend([f"    {item}" for item in python_packages])
    else:
        lines.append("    unavailable")
    lines.append("")

    lines.extend(["## System Packages", "", "    (installed via apt)"])
    if system_packages:
        lines.extend(render_system_package_lines(system_packages))
    else:
        lines.append("    unavailable")
    lines.append("")

    for key in ("Runtime Version Snapshot (Pre-build Probe)", "Runtime Version Snapshot"):
        if key in src:
            lines.extend(_section_block(key, src[key]))
            break

    if "Rich Documentation Links" in src:
        lines.extend(_section_block("Rich Documentation Links", src["Rich Documentation Links"]))

    if "Notes" in src:
        lines.extend(_section_block("Notes", src["Notes"]))

    # Appendix: Artifact Sources and Digests — moved to end for readability.
    # The key changed from "Staged Tool Artifacts" (old) to "Appendix: Artifact Sources
    # and Digests" (new).  Accept both to stay compatible with manifests built before
    # this change.
    appendix_key = "Appendix: Artifact Sources and Digests"
    if appendix_key in src:
        lines.extend(_section_block(appendix_key, src[appendix_key]))

    return "\n".join(lines)


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

    return parser.parse_args()


def _run_installed(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    custom_tooling = read_key_value_file(Path(args.custom_tooling_file))
    python_packages = read_list_file(Path(args.python_packages_file))
    system_packages = read_list_file(Path(args.system_packages_file))

    rendered = render_installed_manifest(
        debian_version=args.debian_version,
        python_version=args.python_version,
        image_version=args.image_version,
        devcontainers_release=args.devcontainers_release,
        devcontainers_version=args.devcontainers_version,
        custom_tooling=custom_tooling,
        python_packages=python_packages,
        system_packages=system_packages,
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
