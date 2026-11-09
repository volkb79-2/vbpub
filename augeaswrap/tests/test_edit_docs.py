"""Keep the public edit contract and fstab adoption docs synchronized."""

from __future__ import annotations

import re
import tomllib
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

    assert "docs/CONSUMERS.md#augeas-backed-edits-and-fstab-helpers" in readme
    assert "docs/DESIGN-GUIDE.md#augeas-edit-resources" in readme
    assert "### Augeas-backed edit behavior" in spec
    assert "edit.augtool.missing" in spec and "edit.duplicate" in spec
    assert "findmnt --verify --tab-file" in spec and "skipped(root)" in spec
    assert "## Augeas edit resources" in design
    assert "fstab helpers are pure request builders" in design
    assert "### Augeas-backed edits and fstab helpers" in consumers
    assert "swap_op([])" in consumers
    assert "Augeas-backed `edit` resource" in changes
    assert "AW-BL-22" in backlog
    assert "Fstab.lns" in backlog and "absent-device findings" in backlog

    section = consumers.split("### Augeas-backed edits and fstab helpers", 1)[1].split("\n### ", 1)[0]
    examples = re.findall(r"```toml\s*\n(.*?)\n```", section, flags=re.DOTALL)
    assert len(examples) == 1
    request_path = tmp_path / "fstab-example.toml"
    request_path.write_text(examples[0] + "\n", encoding="utf-8")
    loaded = load_request(request_path)
    assert loaded.resources[0].kind == "edit"
