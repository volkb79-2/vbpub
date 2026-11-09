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
    assert "Implementation commit:" in changes
    assert "json_key, edit" in package_protocol
    assert "AW-BL-24" in backlog
    assert "CODE_COMMIT" not in backlog
    implementation_commit = "c62372a0005295159308fffc9a44b1b17cfc9483"
    assert implementation_commit in changes
    assert "AW-BL-22 (closed" in backlog and "AW-BL-23 (closed with code change)" in backlog
    assert "AW-BL-24 (closed for the behaviors proven" in backlog
    normalized_backlog = " ".join(backlog.split())
    assert normalized_backlog.count(implementation_commit) == 3
    assert "final live rerun on this fixed branch on a host" in normalized_backlog
    assert "augeas-tools` `1.14.1-1.1~deb13u1" in normalized_backlog
    assert "stat-only block node" in normalized_backlog
    assert "not a complete semantic or runtime check" in " ".join(spec.split())
    assert "LoadState=not-found" in " ".join(spec.split())
    assert "name=value" in " ".join(spec.split())
    gate = (PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8")
    assert "command -v augtool" in gate and "command -v augparse" in gate
    assert "r0-r1 requires augtool and augparse" in gate

    section = consumers.split("### Augeas-backed edits and fstab helpers", 1)[1].split("\n### ", 1)[0]
    examples = re.findall(r"```toml\s*\n(.*?)\n```", section, flags=re.DOTALL)
    assert len(examples) == 1
    request_path = tmp_path / "fstab-example.toml"
    request_path.write_text(examples[0] + "\n", encoding="utf-8")
    loaded = load_request(request_path)
    assert loaded.resources[0].kind == "edit"
