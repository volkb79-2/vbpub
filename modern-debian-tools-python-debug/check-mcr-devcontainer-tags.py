#!/usr/bin/env python3
"""
Check availability of devcontainer python tags on MCR.

If no --debian or --python arguments are supplied, the script checks the
default matrix:
    - debian: bookworm, trixie, forky
    - python: 3.12, 3.13, 3.14, 3.15, 3.16

Examples:
    python3 check-mcr-devcontainer-tags.py
    python3 check-mcr-devcontainer-tags.py --debian bookworm --python 3.11
    python3 check-mcr-devcontainer-tags.py --debian trixie --python 3.14
    python3 check-mcr-devcontainer-tags.py --debian bookworm --python 3.11 --python 3.13

**URLs**
Releases: https://github.com/devcontainers/images/releases
Python image source: https://github.com/devcontainers/images/tree/main/src/python
You can subscribe to repo releases or use GitHub’s RSS for releases.

"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REGISTRY_URL = "https://mcr.microsoft.com/v2/devcontainers/python/tags/list"
MANIFEST_URL = "https://raw.githubusercontent.com/devcontainers/images/main/src/python/manifest.json"
DEFAULT_DEBIANS = ["bookworm", "trixie", "forky"]
DEFAULT_PYTHONS = ["3.12", "3.13", "3.14", "3.15", "3.16"]
TAG_PREFIXES = ["1-", "", "dev-"]
REQUIRED_PREFIX_OPTIONS = {"1-", "", "dev-", "any"}


@dataclass(frozen=True)
class TagCheckResult:
    tag: str
    exists: bool


def build_tag(debian: str, python_version: str, prefix: str) -> str:
    return f"{prefix}{python_version}-{debian}"


def build_tag_variants(debian: str, python_version: str) -> dict[str, str]:
    return {prefix: build_tag(debian, python_version, prefix) for prefix in TAG_PREFIXES}


def fetch_tag_list() -> set[str]:
    try:
        with urllib.request.urlopen(REGISTRY_URL, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to fetch tags from {REGISTRY_URL}: {exc}") from exc

    tags = payload.get("tags")
    if not isinstance(tags, list):
        raise RuntimeError("Unexpected registry response: missing 'tags' list")
    return set(tags)


def fetch_manifest_variants() -> set[str]:
    try:
        with urllib.request.urlopen(MANIFEST_URL, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to fetch manifest from {MANIFEST_URL}: {exc}") from exc

    variants = payload.get("variants")
    if not isinstance(variants, list):
        raise RuntimeError("Unexpected manifest response: missing 'variants' list")
    return set(str(variant).strip() for variant in variants if str(variant).strip())


def manifest_variant_to_tag(variant: str, prefix: str) -> str:
    return f"{prefix}{variant}"


def expected_tags_from_manifest(variants: set[str], prefix: str) -> set[str]:
    return {manifest_variant_to_tag(variant, prefix) for variant in variants}


def check_tags(tags: Iterable[str], available: set[str]) -> list[TagCheckResult]:
    return [TagCheckResult(tag=tag, exists=tag in available) for tag in tags]


def build_matrix(
    debians: list[str],
    python_versions: list[str],
    available: set[str],
) -> dict[str, dict[str, dict[str, bool]]]:
    matrix: dict[str, dict[str, dict[str, bool]]] = {}
    for debian in debians:
        matrix[debian] = {}
        for version in python_versions:
            variants = build_tag_variants(debian, version)
            matrix[debian][version] = {prefix: tag in available for prefix, tag in variants.items()}
    return matrix


def render_table(debians: list[str], python_versions: list[str], matrix: dict[str, dict[str, dict[str, bool]]]) -> str:
    col_widths = [max(8, len("debian"))] + [max(5, len(v)) for v in python_versions]
    header_cells = ["debian".ljust(col_widths[0])]
    for idx, version in enumerate(python_versions, start=1):
        header_cells.append(version.ljust(col_widths[idx]))
    lines = ["  ".join(header_cells)]
    lines.append("  ".join("-" * w for w in col_widths))

    for debian in debians:
        row = [debian.ljust(col_widths[0])]
        for idx, version in enumerate(python_versions, start=1):
            availability = matrix[debian][version]
            flags = "".join(
                flag
                for flag, prefix in (("1", "1-"), ("p", ""), ("d", "dev-"))
                if availability.get(prefix)
            )
            row.append((flags if flags else ".").ljust(col_widths[idx]))
        lines.append("  ".join(row))

    lines.append("\nLegend: 1 = 1- prefix, p = plain, d = dev- prefix, . = missing")
    return "\n".join(lines)


def collect_missing(
    debians: list[str],
    python_versions: list[str],
    matrix: dict[str, dict[str, dict[str, bool]]],
    required_prefix: str,
) -> list[str]:
    missing: list[str] = []
    for debian in debians:
        for version in python_versions:
            availability = matrix[debian][version]
            if required_prefix == "any":
                if not any(availability.values()):
                    missing.append(build_tag(debian, version, ""))
                continue
            if not availability.get(required_prefix, False):
                missing.append(build_tag(debian, version, required_prefix))
    return missing


def write_text(path: str | None, content: str) -> None:
    if not path:
        return
    Path(path).write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check availability of devcontainers/python tags on MCR",
    )
    parser.add_argument(
        "--debian",
        action="append",
        dest="debian_codenames",
        help="Debian codename (e.g., bookworm, trixie). Can be repeated.",
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="python_versions",
        help="Python version (e.g., 3.12, 3.13, 3.14). Can be repeated.",
    )
    parser.add_argument(
        "--json-out",
        help="Write JSON result to file path.",
    )
    parser.add_argument(
        "--table-out",
        help="Write condensed table to file path.",
    )
    parser.add_argument(
        "--required-prefix",
        default="1-",
        help="Prefix to require for exit status: '1-', '', 'dev-', or 'any' (default: 1-).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    debians = [d.strip() for d in (args.debian_codenames or []) if d.strip()]
    python_versions = [v.strip() for v in (args.python_versions or []) if v.strip()]

    if not debians and not python_versions:
        debians = DEFAULT_DEBIANS
        python_versions = DEFAULT_PYTHONS

    if not debians:
        raise ValueError("--debian must specify at least one codename")
    if not python_versions:
        raise ValueError("--python must specify at least one version")

    required_prefix = args.required_prefix.strip()
    if required_prefix not in REQUIRED_PREFIX_OPTIONS:
        raise ValueError("--required-prefix must be one of: '1-', '', 'dev-', 'any'")

    available_tags = fetch_tag_list()
    manifest_variants: set[str] = set()
    manifest_expected_tags: set[str] = set()
    manifest_error: str | None = None
    try:
        manifest_variants = fetch_manifest_variants()
        manifest_prefix = "" if required_prefix == "any" else required_prefix
        manifest_expected_tags = expected_tags_from_manifest(manifest_variants, manifest_prefix)
    except Exception as exc:  # noqa: BLE001
        manifest_error = str(exc)
    matrix = build_matrix(debians, python_versions, available_tags)

    table_output = render_table(debians, python_versions, matrix)
    print(table_output)
    write_text(args.table_out, table_output)

    json_payload = {
        "registry": "mcr.microsoft.com",
        "image": "devcontainers/python",
        "debians": debians,
        "python_versions": python_versions,
        "required_prefix": required_prefix,
        "matrix": matrix,
    }
    if manifest_variants:
        json_payload["manifest_variants"] = sorted(manifest_variants)
        json_payload["manifest_expected_tags"] = sorted(manifest_expected_tags)
    if manifest_error:
        json_payload["manifest_error"] = manifest_error
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

    missing = collect_missing(debians, python_versions, matrix, required_prefix)
    if manifest_error:
        print(f"\n[WARN] Secondary manifest check failed: {manifest_error}")
    if manifest_variants:
        print(f"\nSecondary manifest variants: {len(manifest_variants)} ({MANIFEST_URL})")

    if missing:
        missing_expected = [tag for tag in missing if tag in manifest_expected_tags]
        missing_unexpected = [tag for tag in missing if tag not in manifest_expected_tags]

        if missing_expected:
            print("\nMissing tags (listed in manifest, possibly pending on MCR):")
            for tag in missing_expected:
                print(f"- {tag}")
        if missing_unexpected:
            print("\nMissing tags (not listed in manifest):")
            for tag in missing_unexpected:
                print(f"- {tag}")
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)
