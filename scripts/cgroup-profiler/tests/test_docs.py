"""Mechanical checks for the daemon's three adopter-facing documents."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Dict, Iterable, List

import pytest

import cgprofile as cg
from lib import serve, summary

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "README.md", ROOT / "docs" / "DESIGN-GUIDE.md", ROOT / "docs" / "CONSUMERS.md")


def _json_examples(path: Path) -> Iterable[Dict]:
    text = path.read_text(encoding="utf-8")
    for body in re.findall(r"```json\s*\n(.*?)```", text, flags=re.DOTALL):
        value = json.loads(body)
        assert isinstance(value, dict), f"{path}: JSON example must be an object"
        yield value


def _slug(heading: str) -> str:
    heading = re.sub(r"<[^>]+>", "", heading).lower()
    heading = re.sub(r"[^\w\s-]", "", heading)
    return re.sub(r"[-\s]+", "-", heading).strip("-")


def _anchors(path: Path) -> List[str]:
    return [_slug(match) for match in re.findall(r"^#{1,6}\s+(.+?)\s*$", path.read_text(), re.MULTILINE)]


def test_every_adopter_document_json_example_uses_the_shipped_contract_loader():
    for path in DOCS:
        examples = list(_json_examples(path))
        assert examples, f"{path}: expected a current-schema JSON example"
        for example in examples:
            assert example.get("contract") == serve.CONTRACT_VERSION
            cg._validate_ctl_response("version", example)
        assert summary.SCHEMA == 1


def test_every_local_document_link_and_anchor_resolves():
    link_re = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    for source in DOCS:
        for raw in link_re.findall(source.read_text(encoding="utf-8")):
            if raw.startswith(("http://", "https://", "mailto:")):
                continue
            target_text, _, anchor = raw.partition("#")
            target = (source.parent / target_text).resolve() if target_text else source
            assert target.is_file(), f"{source}: missing linked file {raw}"
            if anchor:
                assert anchor in _anchors(target), f"{source}: missing anchor {raw}"


def test_every_closed_consumer_vocabulary_is_documented():
    text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for value in ("container", "container-shared", "on", "off", "unavailable", "command", "assay"):
        assert re.search(rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])", text)


def test_run_gate_consumer_identity_is_documented_for_all_adopters():
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        assert "installed Run-Gate wheel" in text, path
        assert "SemVer" in text, path
    assert "run-gate --version" in (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")


def test_package_contract_mirrors_canonical_contract_byte_for_byte():
    canonical = ROOT.parents[1] / "run-gate-project" / "nyxloom-trove" / "RG55-INTERFACE-CONTRACT.md"
    local = ROOT / "docs" / "RG55-INTERFACE-CONTRACT.md"
    assert local.read_bytes() == canonical.read_bytes()


_OBSERVER_ENV = (
    "schema_version = 1\n"
    "[environments.tester-unified]\n"
    'mode = "ephemeral"\n'
    'image = "tester-unified:local"\n\n'
)


def _native_r2_lane_example() -> str:
    text = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    blocks = [
        body
        for body in re.findall(r"```toml\s*\n(.*?)```", text, flags=re.DOTALL)
        if 'resource_observer = "cgprofile"' in body
    ]
    assert len(blocks) == 1, "expected exactly one published native-R2 lane example"
    return blocks[0]


def _load_with_shipped_run_gate(tmp_path: Path, lane_text: str) -> None:
    # The installed Run-Gate wheel is part of the tester-unified closure; import
    # it for real so a missing wheel fails loudly rather than skipping the check.
    import run_gate

    project = tmp_path / "project"
    project.mkdir()
    (project / "run-gate.toml").write_text(_OBSERVER_ENV + lane_text, encoding="utf-8")
    run_gate.load_config(project)


def test_published_native_r2_lane_example_loads_with_shipped_run_gate(tmp_path):
    example = _native_r2_lane_example()
    assert 'memory = "2g"' in example and "pids = 8192" in example
    _load_with_shipped_run_gate(tmp_path, example)


@pytest.mark.parametrize("missing", [r'memory = "2g"\n', r"pids = 8192\n"])
def test_shipped_run_gate_rejects_the_example_without_either_resource_ceiling(
    tmp_path, missing
):
    import run_gate

    example = _native_r2_lane_example()
    mutated = re.sub(missing, "", example, count=1)
    assert mutated != example, "the example no longer declares the ceiling under test"
    with pytest.raises(run_gate.GateError, match="requires explicit resources.memory and resources.pids"):
        _load_with_shipped_run_gate(tmp_path, mutated)


def test_release_gate_is_r0_r1_and_r3_only_and_complete_gate_includes_r2():
    cmru = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))
    argvs = [c["argv"] for c in cmru["steps"]["run-tests"]["commands"]]
    assert argvs == [["run-gate", "r0-r1"], ["run-gate", "r3"]]
    lanes = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]
    assert "r2" in lanes["gate"]["argv"][-1]
    assert lanes["r2"]["budget"] == "24h"


def test_every_release_surface_states_there_is_no_full_source_r2_verdict():
    surfaces = (
        ROOT / "CHANGES.md",
        ROOT / "cmru.toml",
        ROOT / "run-gate.toml",
        *DOCS,
    )
    for path in surfaces:
        flat = re.sub(r"\s+", " ", re.sub(r"\n\s*#", "\n", path.read_text(encoding="utf-8"))).lower()
        assert "no completed full-source r2 verdict" in flat, path
