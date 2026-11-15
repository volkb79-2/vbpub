"""The public documents say what the shipped CLI does, and their examples still parse.

None of these checks can be satisfied by intention: every one of them goes red when a
document drifts from the parser, from the declared dependency floor, or from a verb that
exists. That matters because the docs are what an adopter reads before the code.
"""

import re
import tomllib
from pathlib import Path

import pytest

from fleetd.cli import build_cli
from fleetd.contracts import validate_lane_demand
from fleetd.facts import load_facts_config

PROJECT = Path(__file__).parents[1]
PUBLIC_DOCS = [
    PROJECT / "README.md",
    PROJECT / "docs" / "SPEC.md",
    PROJECT / "docs" / "DESIGN-GUIDE.md",
    PROJECT / "docs" / "CONSUMERS.md",
]
PYPROJECT = PROJECT / "pyproject.toml"


def _slug(heading):
    value = re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")
    return re.sub(r"-+", "-", value)


def _declared_dependency() -> str:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirements = pyproject["project"]["dependencies"]
    matches = [item for item in requirements if item.lower().startswith("cli-extended")]
    assert len(matches) == 1, requirements
    return matches[0]


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


# --- the cli-extended adoption, as a documentation obligation ---------------------------


def test_every_public_doc_states_the_same_dependency_floor_as_the_manifest():
    """A doc that quotes a different floor sends an adopter to a library that cannot run it."""

    floor = _declared_dependency()
    for path in PUBLIC_DOCS:
        text = path.read_text(encoding="utf-8")
        if path.name == "DESIGN-GUIDE.md":
            continue  # rationale, not an install instruction; it links to the SPEC clause
        assert floor in text or floor.replace(">=", " ") in text, path


def test_the_readme_and_consumer_guide_carry_a_runnable_host_install():
    """Pasteable, not described: both wheels, offline, and the verification command."""

    for path in (PROJECT / "README.md", PROJECT / "docs" / "CONSUMERS.md"):
        text = path.read_text(encoding="utf-8")
        assert "--no-index" in text, path
        assert "cli_extended-0.5.0-py3-none-any.whl" in text, path
        assert "fleetd-<version>-py3-none-any.whl" in text, path
        assert "fleet --version" in text, path
        assert "exit 3" in text, path


def test_the_spec_has_one_generated_surface_region():
    spec = (PROJECT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert spec.count("<!-- cli-extended-surface:start -->") == 1
    assert spec.count("<!-- cli-extended-surface:end -->") == 1
    assert spec.index("<!-- cli-extended-surface:start -->") < spec.index("<!-- cli-extended-surface:end -->")


@pytest.mark.parametrize("verb", ["facts", "guard", "plan", "run", "status", "tick", "collect", "skills"])
def test_every_registered_verb_is_documented(verb):
    """A capability nobody can find is not shipped: each verb appears in the guides."""

    text = (PROJECT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    assert f"fleet {verb}" in text or f"`fleet {verb}" in text, verb
    assert any(f"fleet {verb}" in path.read_text(encoding="utf-8")
               for path in (PROJECT / "README.md", PROJECT / "docs" / "SPEC.md")), verb


def test_the_consumer_guide_records_every_adoption_checklist_judgement():
    """The checklist rows are part of the change, not a follow-up."""

    text = (PROJECT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    for row in range(1, 26):
        assert f"AC-{row:02d}" in text, f"AC-{row:02d} is unrecorded"
    # The two manual rows must say WHY, not only that they are manual.
    assert "AC-12" in text and "idempotent" in text
    assert "AC-20" in text and "host-facts/1" in text


def test_the_skill_ships_and_the_cli_registers_the_library_verbs():
    skill = PROJECT / "src" / "fleetd" / "skills" / "fleetd-campaigns" / "SKILL.md"
    assert skill.is_file()
    assert build_cli().skills_package == ("fleetd", "skills")
