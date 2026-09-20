"""The adopter-facing CMRU config pair remains loadable by the shipped reader."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from cmru.config import load_forge_config


ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "docs" / "DESIGN-GUIDE.md", ROOT / "docs" / "CONSUMERS.md"]


def _slug(heading: str) -> str:
    value = re.sub(r"^#+\s*", "", heading.strip()).lower()
    value = re.sub(r"[^\w\s-]", "", value)
    return value.replace(" ", "-")


def test_every_documented_toml_example_is_current_toml_and_versioned():
    for document in DOCS:
        text = document.read_text(encoding="utf-8")
        blocks = re.findall(r"```toml\n(.*?)```", text, flags=re.DOTALL)
        assert blocks, f"{document} has no TOML example"
        for block in blocks:
            parsed = tomllib.loads(block)
            assert parsed.get("schema_version") == 1, document


def test_runtime_vocabulary_and_workspace_contract_are_documented():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for value in ("kind = \"none\"", "kind = \"ciu\"", "per Git family", "workspace-id"):
        assert value in corpus
    for document in (ROOT / "README.md", ROOT / "docs" / "SPEC.md", ROOT / "docs" / "RELEASE-TRANSACTIONS.md"):
        text = document.read_text(encoding="utf-8")
        assert "cmru-build-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>" in text


def test_assay_lane_declares_the_complete_rigor_ladder():
    lane = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))["lanes"]["cmru"]
    assert lane["rigor"] == ["R0", "R1", "R2", "R3"]
    assert lane["isolation"]["snapshot_selection"] == "repository-minus-unsafe-symlinks"
    assert lane["judge"]["coverage"]["artifact"] == "coverage.json"
    assert lane["judge"]["mutation"]["jobs"] == 1
    assert lane["judge"]["canary"]["mechanism"] == "import-break"


def test_cross_document_links_resolve():
    headings: dict[Path, set[str]] = {}
    failures: list[str] = []
    for document in DOCS:
        headings[document] = {
            _slug(line) for line in document.read_text(encoding="utf-8").splitlines()
            if line.lstrip().startswith("#")
        }
    for document in DOCS:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if target.startswith(("http", "#")):
                continue
            path_part, _, anchor = target.partition("#")
            resolved = (document.parent / path_part).resolve()
            if not resolved.exists():
                failures.append(f"{document}: {target}")
            elif anchor:
                if resolved not in headings:
                    headings[resolved] = {
                        _slug(line) for line in resolved.read_text(encoding="utf-8").splitlines()
                        if line.lstrip().startswith("#")
                    }
                if anchor not in headings[resolved]:
                    failures.append(f"{document}: {target}")
    assert failures == []


def test_consumers_central_config_example_is_complete_and_loadable(tmp_path: Path):
    document = (
        Path(__file__).resolve().parents[1] / "docs" / "CONSUMERS.md"
    ).read_text(encoding="utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", document, flags=re.DOTALL)
    assert len(blocks) >= 2

    (tmp_path / "example-wheel").mkdir()
    (tmp_path / "example-wheel" / "cmru.toml").write_text(blocks[0], encoding="utf-8")
    central = tmp_path / "cmru.orchestration.toml"
    central.write_text(blocks[1], encoding="utf-8")

    config = load_forge_config(central, require_orchestration=True)
    assert config.orchestration is not None
    assert config.orchestration.execution_mode == "project-first"
    assert config.cleanup is not None
    assert config.projects["example-wheel"].name == "example-wheel"
