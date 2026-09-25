"""The adopter-facing CMRU config pair remains loadable by the shipped reader."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from cmru.config import load_forge_config
from cmru.version_config import SOURCE_FIELDS


ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "docs" / "DESIGN-GUIDE.md", ROOT / "docs" / "CONSUMERS.md"]


def _slug(heading: str) -> str:
    value = re.sub(r"^#+\s*", "", heading.strip()).lower()
    value = re.sub(r"[^\w\s-]", "", value)
    return value.replace(" ", "-")


def test_every_documented_toml_example_is_current_toml_and_versioned():
    block_counts = {}
    for document in DOCS:
        text = document.read_text(encoding="utf-8")
        blocks = re.findall(r"```toml\n(.*?)```", text, flags=re.DOTALL)
        block_counts[document.name] = len(blocks)
        for block in blocks:
            parsed = tomllib.loads(block)
            assert parsed.get("schema_version") == 1, document
    # The consumer document owns the only complete config pair. README and the
    # design guide link to it rather than publishing partial TOML fragments
    # that cannot pass the installed reader.
    assert block_counts == {"README.md": 0, "DESIGN-GUIDE.md": 0, "CONSUMERS.md": 2}


def test_runtime_vocabulary_and_workspace_contract_are_documented():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for value in ("kind = \"none\"", "kind = \"ciu\"", "per Git family", "workspace-id"):
        assert value in corpus
    for document in (ROOT / "README.md", ROOT / "docs" / "SPEC.md", ROOT / "docs" / "RELEASE-TRANSACTIONS.md"):
        text = document.read_text(encoding="utf-8")
        assert "cmru-build-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>" in text


def test_version_policy_vocabulary_is_documented():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for source_type in SOURCE_FIELDS:
        assert f".{source_type}" in corpus
    for value in ('mode = "single"', 'mode = "aligned"'):
        assert value in corpus
    for value in ('selection = "semver"', 'selection = "rolling"', 'Docker-Content-Digest'):
        assert value in corpus
    assert 'vnd.docker.reference.type = "attestation-manifest"' in corpus
    assert "commit time rather than the proxy publication time" in corpus
    assert "age-window refusal" in corpus
    for field in (
        "age_window_days", "constraint", "version", "reason", "expires", "resolved",
        "pypi_extras", "requirements_files", "digest",
    ):
        assert field in corpus
    for value in ('scope = "shipped"', 'scope = "all"'):
        assert value in corpus


def test_assay_and_release_gate_split_rigor_without_empty_release_mutation():
    lane = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))["lanes"]["cmru"]
    assert lane["rigor"] == ["R0", "R1", "R3"]
    assert "--maxfail=1" in lane["argv"]
    assert lane["isolation"]["snapshot_selection"] == "repository-minus-unsafe-symlinks"
    assert lane["judge"]["coverage"]["artifact"] == "coverage.json"
    assert "mutation" not in lane["judge"]
    assert lane["judge"]["canary"]["mechanism"] == "import-break"

    gate = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    gate_command = " ".join(gate["lanes"]["gate"]["argv"])
    for name in ("assay", "coverage", "mutation", "canary", "enroll"):
        assert f"./run-gate.py --worktree {{worktree}} {name}" in gate_command
    mutation_command = " ".join(gate["lanes"]["mutation"]["argv"])
    assert "git describe --tags --abbrev=0 --match 'cmru-v*'" in mutation_command
    assert 'git diff --quiet "$BASE"..HEAD -- src' in mutation_command
    assert '"reason": "no-changed-source"' in mutation_command
    assert "--require-candidates" in mutation_command

    for name in ("coverage", "mutation", "canary"):
        assert "--maxfail=1" in " ".join(gate["lanes"][name]["argv"])

    for document in (
        ROOT / "README.md",
        ROOT / "docs" / "SPEC.md",
        ROOT / "docs" / "DESIGN-GUIDE.md",
        ROOT / "docs" / "CONSUMERS.md",
    ):
        text = document.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        assert "--maxfail=1" in text
        assert "120 seconds" in text or "120-second" in text
        assert "progress" in text.lower()
        assert "nearest ancestor" in normalized
        assert "cmru-v*" in text


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
    assert config.versions["age_window_days"] == 14
    assert config.versions["discovery"]["scope"] == "shipped"
    assert config.versions["targets"]["pypi.requests"]["pypi"]["name"] == "requests"
    project_versions = config.projects["example-wheel"].versions
    assert project_versions["age_window_days"] == 21
    assert project_versions["discovery"]["scope"] == "shipped"
    assert project_versions["targets"]["pypi.requests"]["mode"] == "single"
    assert project_versions["targets"]["oci.node"]["oci"]["selection"] == "rolling"
    assert project_versions["targets"]["go.golang.org.x.text"]["go"]["module"] == "golang.org/x/text"


def test_registered_projects_track_their_shared_pwmcp_runtime_image():
    workspace = ROOT.parent
    for project in ("topos", "nyxloom"):
        config = tomllib.loads((workspace / project / "cmru.toml").read_text(encoding="utf-8"))
        target = config["versions"]["targets"]["oci.pwmcp"]
        assert target["mode"] == "single"
        assert target["oci"]["image"] == "ghcr.io/volkb79-2/pwmcp"
        assert target["oci"]["tag"] == "{version}"
