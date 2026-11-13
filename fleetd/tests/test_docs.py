import re
import tomllib
from pathlib import Path

from fleetd.contracts import validate_lane_demand
from fleetd.facts import load_facts_config


PROJECT = Path(__file__).parents[1]
PUBLIC_DOCS = [
    PROJECT / "README.md",
    PROJECT / "docs" / "SPEC.md",
    PROJECT / "docs" / "DESIGN-GUIDE.md",
    PROJECT / "docs" / "CONSUMERS.md",
]


def _slug(heading):
    value = re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")
    return re.sub(r"-+", "-", value)


def test_public_docs_toml_examples_parse_and_declare_their_schema(tmp_path):
    for path in PUBLIC_DOCS:
        text = path.read_text(encoding="utf-8")
        blocks = re.findall(r"```toml\s+([\s\S]*?)```", text)
        for block in blocks:
            config = tomllib.loads(block)
            assert config.get("schema_version") == 1, path
            if "paths" in config:
                facts_path = tmp_path / "etc" / "fleetd" / "facts.toml"
                facts_path.parent.mkdir(parents=True, exist_ok=True)
                facts_path.write_text(block, encoding="utf-8")
                parsed_facts = load_facts_config(tmp_path)
                assert parsed_facts.overcommit == 1.5
                assert parsed_facts.psi_refuse_some_avg10 == 25
                continue
            assert "lanes" in config and "r2" in config["lanes"], path
            validate_lane_demand(config["lanes"]["r2"]["demand"], f"{path}:lanes.r2.demand")


def test_public_doc_links_and_anchors_resolve():
    for source in PUBLIC_DOCS:
        text = source.read_text(encoding="utf-8")
        for destination, anchor in re.findall(r"\]\(([^)#]+)(?:#([^)]*))?\)", text):
            target = (source.parent / destination).resolve()
            assert target.is_file(), f"{source}: missing {destination}"
            if anchor:
                headings = {
                    _slug(match.group(1))
                    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", target.read_text(encoding="utf-8"), re.MULTILINE)
                }
                assert anchor in headings, f"{source}: missing anchor #{anchor} in {destination}"


def test_docs_keep_the_first_week_boundary_and_closed_vocabularies_visible():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_DOCS)
    for value in ("candidate", "worker", "run", "report", "enforce", "least-progress-per-ram", "host-facts/1", "work-request/1", "assay-merge-manifest/1"):
        assert value in combined
    assert "no daemon" in combined.lower()
    assert "--only-candidates FILE" in combined
