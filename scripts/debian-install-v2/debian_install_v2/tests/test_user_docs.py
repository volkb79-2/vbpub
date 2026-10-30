from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path
from urllib.parse import unquote, urlsplit

from debian_install_v2.config import (
    CLOUD_INIT_CLEANUP_MODES,
    SCHEMA_VERSION,
    Config,
    load_config,
)
from debian_install_v2.profiles import list_builtin_profiles, load_builtin_profile


PROJECT = Path(__file__).resolve().parents[2]
DOCS = {
    PROJECT / "README.md",
    PROJECT / "docs" / "DESIGN-GUIDE.md",
    PROJECT / "docs" / "CONSUMERS.md",
}
CONFIG_KEYS = {item.name for item in fields(Config)}


def _markdown_config_examples(path: Path):
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(
        r"(?ms)^```(?P<language>json)[^\n]*\n(?P<body>.*?)^```",
        text,
    ):
        value = json.loads(match.group("body"))
        if isinstance(value, dict) and CONFIG_KEYS.intersection(value):
            yield value

    for match in re.finditer(r"--config-json\s*\\\s*'([^']*)'", text, re.S):
        yield json.loads(match.group(1))


def test_all_documented_config_examples_use_the_current_loader_and_schema():
    count = 0
    for path in DOCS:
        for example in _markdown_config_examples(path):
            count += 1
            assert example.get("schema_version") == SCHEMA_VERSION, path
            load_config(raw_json=json.dumps(example))
    assert count >= 2


def test_every_cloud_init_cleanup_mode_is_named_in_user_docs():
    text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for mode in CLOUD_INIT_CLEANUP_MODES:
        assert f"`{mode}`" in text


def test_every_root_and_swap_policy_value_is_named_in_user_docs():
    text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    assert "`root_resize_policy`" in text
    assert "`swap_policy`" in text
    for value in ("resize", "untouched", "grow-only", "on", "off"):
        assert f"`{value}`" in text


def test_each_user_doc_discloses_that_managed_swap_takes_over_guest_swap_state():
    for path in DOCS:
        text = path.read_text(encoding="utf-8").lower()
        assert re.search(r"all (?:currently )?active swap", text), path
        assert "logical volume" in text or "logical-volume" in text, path
        assert re.search(r"removes\s+configured\s+swapfiles", text), path
        assert re.search(
            r"(?:retained pre-root swap partition|pre-root swap partition.{0,100}retained)",
            text,
            re.S,
        ), path


def test_swap_size_formula_and_pre_root_count_are_documented_for_consumers():
    consumers = (PROJECT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    assert "RAM × 2" in consumers
    assert "20% of disk capacity" in consumers
    assert re.search(r"counts\s+toward both the requested total size", consumers)
    assert "default total count of four" in consumers


def test_each_user_doc_says_root_shrink_safety_precedes_swapfile_cleanup():
    for path in DOCS:
        text = path.read_text(encoding="utf-8").lower()
        assert "root-shrink safety is checked before swapfile cleanup" in text, path


def test_released_profile_files_parse_with_loader_and_are_named_in_user_docs():
    text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for profile_id in list_builtin_profiles():
        profile = load_builtin_profile(profile_id)
        assert profile.get("schema_version") == SCHEMA_VERSION
        load_config(raw_json=json.dumps(profile))
        assert f"`{profile_id}`" in text


def _slug(heading: str) -> str:
    heading = re.sub(r"`([^`]*)`", r"\1", heading)
    heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
    heading = re.sub(r"<[^>]+>", "", heading).lower()
    heading = re.sub(r"[^a-z0-9 _-]", "", heading)
    return heading.strip().replace(" ", "-")


def _heading_slugs(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {
        _slug(match.group(1))
        for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", text)
    }


def test_cross_document_heading_anchors_resolve():
    resolved_docs = {path.resolve() for path in DOCS}
    for source in DOCS:
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = match.group(1).split()[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.fragment:
                continue
            target_path = (
                (source.parent / unquote(parsed.path)).resolve()
                if parsed.path
                else source.resolve()
            )
            if target_path not in resolved_docs:
                continue
            assert unquote(parsed.fragment) in _heading_slugs(target_path), (
                source,
                target,
            )
