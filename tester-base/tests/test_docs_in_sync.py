"""The docs triad of tester-base is part of the change (estate rule, mdt-cockpit's test_docs_in_sync
is the precedent): examples parse, every closed vocabulary a consumer types is documented, and every
cross-document anchor - including the one tester-unified's README makes into the skip-diff section -
resolves. No docker needed."""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DOCS = [ROOT / "README.md", ROOT / "docs" / "DESIGN-GUIDE.md", ROOT / "docs" / "CONSUMERS.md"]
LINKING = DOCS + [REPO / "tester-unified" / "README.md"]


def consumer_base_args() -> dict[str, str]:
    """{consumer Dockerfile: ARG name} for every shipped consumer whose base ARG defaults to tester-base:local."""
    found = {}
    for relative in ("tester-unified/Dockerfile", "wheel-builder/Dockerfile"):
        text = (REPO / relative).read_text(encoding="utf-8")
        match = re.search(r"^ARG (\w+)=tester-base:local$", text, re.MULTILINE)
        if match:
            found[relative] = match.group(1)
    return found


def text_of(paths: list[Path]) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation except hyphens, spaces to hyphens."""
    heading = re.sub(r"`", "", heading.strip().lower())
    heading = re.sub(r"[^\w\- ]", "", heading)
    return heading.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)}


class DocsTriad(unittest.TestCase):
    def test_the_three_documents_exist_and_have_one_job_each(self) -> None:
        for path in DOCS:
            self.assertTrue(path.is_file(), path)
        self.assertIn("# tester-base", DOCS[0].read_text(encoding="utf-8"))
        self.assertIn("design guide", DOCS[1].read_text(encoding="utf-8").lower())
        self.assertIn("consumers guide", DOCS[2].read_text(encoding="utf-8").lower())

    def test_every_toml_example_parses_with_the_shipped_loader(self) -> None:
        blocks = re.findall(r"```toml\n(.*?)```", text_of(LINKING), re.DOTALL)
        for block in blocks:
            tomllib.loads(block)
        # the shipped configs themselves are the examples a reader copies
        for name in ("cmru.toml", "run-gate.toml"):
            tomllib.loads((ROOT / name).read_text(encoding="utf-8"))

    def test_every_dockerfile_example_matches_the_shipped_pattern(self) -> None:
        shipped = consumer_base_args()
        self.assertGreaterEqual(len(shipped), 2, f"expected tester-unified and wheel-builder, found {shipped}")
        for block in re.findall(r"```dockerfile\n(.*?)```", text_of(DOCS), re.DOTALL):
            declared = re.findall(r"^ARG (\w+)=tester-base:local$", block, re.MULTILINE)
            self.assertTrue(declared, "a dockerfile example must declare the base ARG with its tester-base:local default")
            for name in declared:
                self.assertIn(name, shipped.values(), f"{name} is not a base ARG of any shipped consumer Dockerfile")
                self.assertIn(f"FROM ${{{name}}}", block)

    def test_the_docs_name_the_real_pin_site_of_every_shipped_consumer(self) -> None:
        docs = text_of(DOCS)
        for dockerfile, name in consumer_base_args().items():
            self.assertIn(f"ARG {name}=", docs, f"{dockerfile}: the pin-site ARG {name} is not named in the docs")
        self.assertNotIn("TESTER_BASE=", docs.replace("TESTER_BASE_IMAGE=", ""))
        self.assertNotIn("ARG TESTER_BASE", docs)

    def test_every_vocabulary_value_a_consumer_types_is_documented(self) -> None:
        docs = text_of(DOCS)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        lanes = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]
        typed = set(re.findall(r"^ARG (\w+)=", dockerfile, re.MULTILINE))  # build args a rebuild may override
        typed |= set(re.findall(r"^ENV (\w+)=", dockerfile, re.MULTILINE))
        for lane, body in lanes.items():
            typed.add(lane)
            typed |= set(body.get("required_env", []))
        typed |= set(consumer_base_args().values())  # read from the shipped consumer Dockerfiles
        typed |= {"BUILDX_BUILDER"}
        # the digest/checksum ARGs are documented as a group; each name must still appear somewhere
        missing = sorted(name for name in typed if name not in docs)
        self.assertEqual(missing, [], f"undocumented vocabulary: {missing}")

    def test_the_documented_tools_equal_the_contract_lanes_positive_list(self) -> None:
        readme = DOCS[0].read_text(encoding="utf-8")
        contract = (ROOT / "tests" / "test_contract.py").read_text(encoding="utf-8")
        for tool in ("docker-ce-cli", "minisign", "shellcheck"):
            self.assertIn(tool, readme)
        for tool in ("crane", "regctl", "tc", "systemd-tmpfiles", "fdisk", "zsh", "nvim", "node", "php", "systemctl"):
            self.assertIn(f'"{tool}"', contract, f"{tool} is in no negative assertion")

    def test_every_relative_link_and_anchor_resolves(self) -> None:
        for path in LINKING:
            for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
                if re.match(r"[a-z]+://|mailto:", target):
                    continue
                file_part, _, fragment = target.partition("#")
                resolved = path if not file_part else (path.parent / file_part).resolve()
                self.assertTrue(resolved.is_file() or resolved.is_dir(), f"{path.name}: {target} does not exist")
                if fragment and resolved.suffix == ".md":
                    self.assertIn(fragment, anchors(resolved), f"{path.name}: anchor #{fragment} is not a heading of {resolved.name}")

    def test_tester_unified_readme_links_into_the_skip_diff_section(self) -> None:
        readme = (REPO / "tester-unified" / "README.md").read_text(encoding="utf-8")
        target = "../tester-base/docs/DESIGN-GUIDE.md#what-the-skip-diff-found-mdtc-02-t8-and-where-each-answer-lives"
        self.assertTrue(f"]({target})" in readme, "tester-unified/README.md has no link into the skip-diff section")
        self.assertIn(target.split("#")[1], anchors(ROOT / "docs" / "DESIGN-GUIDE.md"))

    def test_the_slug_function_matches_known_github_anchors(self) -> None:
        self.assertEqual(slug("What the skip-diff found (MDTC-02 T8) and where each answer lives"),
                         "what-the-skip-diff-found-mdtc-02-t8-and-where-each-answer-lives")
        self.assertEqual(slug("Bootstrap order (nothing published yet)"), "bootstrap-order-nothing-published-yet")


if __name__ == "__main__":
    unittest.main()
