import re
from pathlib import Path

import pytest

from tnt.profile import load_profiles


ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "README.md", ROOT / "docs/DESIGN.md", ROOT / "docs/CONSUMERS.md")


@pytest.mark.parametrize("doc", DOCS, ids=lambda path: path.name)
def test_documented_toml_profiles_parse_with_the_shipped_loader(tmp_path, doc):
    blocks = re.findall(r"```toml\s*\n(.*?)```", doc.read_text(), flags=re.DOTALL)
    assert blocks, f"{doc} has no parseable profile example"
    for index, body in enumerate(blocks):
        path = tmp_path / f"{doc.stem}-{index}.toml"
        path.write_text(body)
        path.chmod(0o600)
        profiles, _ = load_profiles(path)
        assert profiles


def test_committed_profile_example_parses_with_the_shipped_loader(tmp_path):
    path = tmp_path / "tnt.toml"
    path.write_text((ROOT / "tnt.toml.example").read_text())
    path.chmod(0o600)
    profiles, _ = load_profiles(path)
    assert set(profiles) == {"lab", "password-auth"}


def _anchor(text, label):
    slug = re.sub(r"[^\w -]", "", label.lower()).strip().replace(" ", "-")
    slug = re.sub(r"-+", "-", slug)
    return slug in {m.group(1) for m in re.finditer(r"<a\s+id=[\"']([^\"']+)", text, re.I)} | {
        re.sub(r"[^\w -]", "", heading.lower()).strip().replace(" ", "-")
        for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M)
    }


def test_cross_document_links_and_anchors_resolve():
    for source in DOCS:
        text = source.read_text()
        for target, anchor in re.findall(r"\]\(([^)#]+\.md)(?:#([^)]*))?\)", text):
            resolved = (source.parent / target).resolve()
            assert resolved.is_file(), f"{source}: missing linked document {target}"
            if anchor:
                linked_text = resolved.read_text()
                assert _anchor(linked_text, anchor.replace("-", " ")), f"{source}: missing anchor {target}#{anchor}"

