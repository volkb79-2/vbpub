from __future__ import annotations

import re
from pathlib import Path

import tomllib
from patchseries import __version__, check, load_vocabulary, parse, render

DOCS = Path(__file__).parents[1]
USER_DOCS = ("README.md", "DESIGN-GUIDE.md", "CONSUMERS.md")


def _slug(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"[^a-z0-9 -]", "", text)
    return re.sub(r"\s+", "-", text)


def test_toml_config_examples_use_the_current_loader_schema(tmp_path: Path) -> None:
    for name in USER_DOCS:
        content = (DOCS / name).read_text(encoding="utf-8")
        for index, match in enumerate(
            re.finditer(r"```toml\s*\n(.*?)\n```", content, re.DOTALL)
        ):
            parsed = tomllib.loads(match.group(1))
            assert parsed["schema_version"] == 1
            vocabulary = tmp_path / f"{name}-{index}.toml"
            vocabulary.write_text(match.group(1), encoding="utf-8")
            assert set(load_vocabulary(vocabulary)) == {
                "capabilities",
                "roles",
                "risks",
            }


def test_readme_cross_document_anchors_resolve() -> None:
    content = (DOCS / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\(([^)#]+\.md)#([^)]+)\)", content)

    assert links
    for target, anchor in links:
        target_path = DOCS / target
        headings = {
            _slug(match.group(1))
            for match in re.finditer(
                r"^#{1,6}\s+(.+?)\s*#*\s*$", target_path.read_text(), re.MULTILINE
            )
        }
        assert anchor in headings


def test_wings_readme_cross_document_anchors_resolve() -> None:
    wings = DOCS.parents[1] / "wings-patchstack"
    content = (wings / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\(([^)#]+\.md)#([^)]+)\)", content)

    assert links
    for target, anchor in links:
        target_path = wings / target
        headings = {
            _slug(match.group(1))
            for match in re.finditer(
                r"^#{1,6}\s+(.+?)\s*#*\s*$",
                target_path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        }
        assert anchor in headings


def test_documented_role_vocabulary_is_complete() -> None:
    content = "\n".join((DOCS / name).read_text(encoding="utf-8") for name in USER_DOCS)

    for value in ("enable", "guard", "plumbing"):
        assert f'"{value}"' in content


def test_patch_shapes_dependency_order_and_digest_contract_are_documented() -> None:
    readme = (DOCS / "README.md").read_text(encoding="utf-8")
    design = (DOCS / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (DOCS / "CONSUMERS.md").read_text(encoding="utf-8")

    assert "rename-only" in readme and "mode-only" in readme
    assert "binary patches are unsupported" in design
    assert "must appear earlier" in design
    assert "ordered patch bytes" in design
    assert "Filenames and the `patchseries` package version are not" in design
    assert "series_digest(series," in consumers
    assert "library version" in consumers


def test_distribution_and_digest_versions_match() -> None:
    project = tomllib.loads((DOCS / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == __version__


def test_wings_readme_inventory_matches_rendered_patch_trailers() -> None:
    repo = DOCS.parents[1]
    wings = repo / "wings-patchstack"
    columns = [
        (
            "pterodactyl-v1.13.3",
            parse(wings / "patches/pterodactyl-v1.13.3"),
        ),
        (
            "filesystem-pterodactyl-v1.13.1",
            parse(wings / "patches/filesystem/pterodactyl-v1.13.1"),
        ),
    ]
    vocabulary = load_vocabulary(wings / "vocabulary.toml")
    for _, changes in columns:
        check(changes, vocabulary)
    readme = (wings / "README.md").read_text(encoding="utf-8")
    start_marker = "<!-- patchseries: inventory:start -->"
    end_marker = "<!-- patchseries: inventory:end -->"
    generated = readme.split(start_marker, 1)[1].split(end_marker, 1)[0].strip()

    assert generated == render(columns).strip()
