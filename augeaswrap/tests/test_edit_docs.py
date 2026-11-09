"""Keep the public edit contract and fstab adoption docs synchronized."""

from __future__ import annotations

import re
from pathlib import Path

from augeaswrap import load_request

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_edit_docs_link_feature_rationale_adoption_and_owed_smoke(tmp_path):
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    spec = (PROJECT_ROOT / "SPEC.md").read_text(encoding="utf-8")
    consumers = (PROJECT_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    changes = (PROJECT_ROOT / "CHANGES.md").read_text(encoding="utf-8")
    backlog = (PROJECT_ROOT / "BACKLOG.md").read_text(encoding="utf-8")
    package_protocol = (PROJECT_ROOT / "src" / "augeaswrap" / "__init__.py").read_text(encoding="utf-8")

    assert "docs/CONSUMERS.md#augeas-backed-edits-and-fstab-helpers" in readme
    assert "docs/DESIGN-GUIDE.md#augeas-edit-resources" in readme
    assert "### Augeas-backed edit behavior" in spec
    assert "edit.augtool.missing" in spec and "edit.duplicate" in spec
    assert "findmnt --verify --tab-file" in spec and "skipped(root)" in spec
    normalized_spec = " ".join(spec.split())
    assert "earlier operations; `records` items also reobserve the staged tree" in normalized_spec
    assert "verification. `file`, `json_key`, `edit`, `unit`, `sysctl`, `sysusers`, `tmpfiles`, and `endpoint` implement kind-specific restore behavior" in normalized_spec
    assert "Kinds currently revertible: `file`, `json_key`, `edit`" in normalized_spec
    assert "## Augeas edit resources" in design
    assert "fstab helpers are pure request builders" in design
    normalized_design = " ".join(design.split())
    assert "current staged tree before it selects a sequence path" in normalized_design
    assert "hand-built mappings" in consumers
    assert "staged result of earlier operations" in " ".join(consumers.split())
    assert "staged effect of earlier" in readme
    assert "### Augeas-backed edits and fstab helpers" in consumers
    assert "swap_op([])" in consumers
    assert "Augeas-backed `edit` resource" in changes
    assert "json_key, edit" in package_protocol
    assert "AW-BL-24" in backlog
    assert "Fstab.lns" in backlog and "absent-device findings" in backlog

    section = consumers.split("### Augeas-backed edits and fstab helpers", 1)[1].split("\n### ", 1)[0]
    examples = re.findall(r"```toml\s*\n(.*?)\n```", section, flags=re.DOTALL)
    assert len(examples) == 1
    request_path = tmp_path / "fstab-example.toml"
    request_path.write_text(examples[0] + "\n", encoding="utf-8")
    loaded = load_request(request_path)
    assert loaded.resources[0].kind == "edit"
