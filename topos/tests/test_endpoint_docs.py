"""Keep the endpoint feature discoverable, explainable, and adoptable."""

from __future__ import annotations

import re
from pathlib import Path

from topos.service.config import parse_config


TOPOS_ROOT = Path(__file__).resolve().parents[1]


def _heading_anchor_exists(document: str, anchor: str) -> bool:
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", document, flags=re.MULTILINE):
        plain = re.sub(r"[`*_]", "", heading).lower()
        plain = re.sub(r"[^a-z0-9 -]", "", plain)
        if re.sub(r"\s+", "-", plain.strip()) == anchor:
            return True
    return False


def _assert_local_markdown_links_resolve(document_path: Path) -> None:
    text = document_path.read_text(encoding="utf-8")
    for href in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        if href.startswith(("http:", "https:", "mailto:")):
            continue
        target, marker, anchor = href.partition("#")
        target_path = (document_path.parent / target).resolve() if target else document_path
        assert target_path.is_file(), f"{document_path}: missing linked file {href}"
        if marker:
            assert _heading_anchor_exists(target_path.read_text(encoding="utf-8"), anchor), (
                f"{document_path}: missing linked heading {href}"
            )


def test_readme_feature_links_to_rationale_and_consumer_examples() -> None:
    readme = (TOPOS_ROOT / "README.md").read_text(encoding="utf-8")
    design = (TOPOS_ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (TOPOS_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")

    design_anchor = "host-endpoint-record-and-client-resolution"
    consumers_anchor = "connect-a-client"
    assert f"docs/DESIGN-GUIDE.md#{design_anchor}" in readme
    assert f"docs/CONSUMERS.md#{consumers_anchor}" in readme
    assert _heading_anchor_exists(design, design_anchor)
    assert _heading_anchor_exists(consumers, consumers_anchor)
    assert "TOPOS_OBSERVE_SOCKET" in readme
    assert "TOPOS_OBSERVE_SOCKET" in design
    assert "TOPOS_OBSERVE_SOCKET" in consumers
    assert "doctor --check endpoint-record" in consumers
    for path in (
        TOPOS_ROOT / "README.md",
        TOPOS_ROOT / "docs" / "DESIGN-GUIDE.md",
        TOPOS_ROOT / "docs" / "CONSUMERS.md",
    ):
        _assert_local_markdown_links_resolve(path)


def test_consumer_service_config_example_parses_with_the_shipped_loader() -> None:
    consumers = (TOPOS_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    examples = re.findall(r"```toml\s*\n(.*?)```", consumers, flags=re.DOTALL)

    assert examples
    parsed = [parse_config(example, source="docs/CONSUMERS.md") for example in examples]
    assert parsed[0].sockets.observe.path.as_posix() == "/run/topos/observe/topos.sock"


def test_adoption_docs_explain_environment_source_and_unset_refusal() -> None:
    consumers = (TOPOS_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    operations = (TOPOS_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    daemon = (TOPOS_ROOT / "docs" / "DAEMON.md").read_text(encoding="utf-8")

    for document in (consumers, operations, daemon):
        assert "TOPOS_OBSERVE_SOCKET" in document
        assert "/etc/host-endpoints.env" in document
        assert "usage error" in document
    assert "--attach PATH" in consumers
    assert "--socket PATH" in consumers
    assert "doctor --check endpoint-record" in daemon
