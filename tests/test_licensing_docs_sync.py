"""Licensing docs-sync: shipped projects declare a licence and no source file contradicts it.

Guards the policy in docs/LICENSING.md:

* every shipped Python project (a tracked ``pyproject.toml`` outside LEGACY) declares an SPDX
  ``license`` expression and carries a ``LICENSE`` file that is byte-equal to the root ``LICENSE``;
* an ``SPDX-License-Identifier`` header on a tracked file is either the first-party id or the file is
  listed in ``THIRD_PARTY_SPDX`` (a vendored file that keeps its upstream licence);
* docs/LICENSING.md names every project, so a new project cannot ship unlisted.

Plain stdlib; run from anywhere inside the checkout: ``python3 -m pytest tests/test_licensing_docs_sync.py``.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FIRST_PARTY_ID = "MIT"

#: Projects that are not shipped (archived experiments); they may lack licence metadata.
LEGACY_PREFIXES = ("legacy-experiments/",)

#: Tracked files that legitimately carry a non-first-party SPDX id. Path -> expected id. Empty today: the
#: vendored iocost generator carries a copyright header, not an SPDX line (see docs/LICENSING.md).
THIRD_PARTY_SPDX: dict[str, str] = {}

_SPDX = re.compile(r"^\s*(?:#|//|/\*|\*|--|;)\s*SPDX-License-Identifier:\s*(?P<id>[^*]+?)\s*(?:\*/)?\s*$")


def _tracked() -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=False
    )
    if proc.returncode != 0:
        pytest.skip("not inside a git checkout")
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def _projects() -> list[str]:
    return sorted(
        p for p in _tracked()
        if p.endswith("pyproject.toml") and not p.startswith(LEGACY_PREFIXES)
    )


def test_root_license_is_mit_with_a_holder():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert re.search(r"^Copyright \(c\) [0-9-]+ \S+", text, re.M)


@pytest.mark.parametrize("pyproject", _projects())
def test_every_shipped_project_declares_the_licence(pyproject):
    data = tomllib.loads((ROOT / pyproject).read_text(encoding="utf-8"))
    project = data.get("project", {})
    assert project.get("license") == FIRST_PARTY_ID, f"{pyproject}: [project].license must be {FIRST_PARTY_ID!r}"
    assert "license-files" not in project or "LICENSE" in project["license-files"]
    licence = (ROOT / pyproject).parent / "LICENSE"
    assert licence.is_file(), f"{licence.relative_to(ROOT)} missing (copy of the root LICENSE)"
    assert licence.read_bytes() == (ROOT / "LICENSE").read_bytes(), f"{licence.relative_to(ROOT)} differs from root LICENSE"


def test_no_source_file_carries_a_conflicting_spdx_id():
    conflicts = []
    for rel in _tracked():
        path = ROOT / rel
        if not path.is_file() or path.is_symlink():
            continue
        try:
            head = path.read_bytes()[:2048].decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line in head.splitlines()[:12]:
            m = _SPDX.match(line)
            if not m:
                continue
            found = m.group("id")
            expected = THIRD_PARTY_SPDX.get(rel, FIRST_PARTY_ID)
            if found != expected:
                conflicts.append(f"{rel}: {found} (expected {expected})")
            break
    assert not conflicts, "conflicting SPDX identifiers:\n" + "\n".join(conflicts)


def test_third_party_spdx_entries_still_exist():
    tracked = set(_tracked())
    assert [p for p in THIRD_PARTY_SPDX if p not in tracked] == []


def test_licensing_doc_names_every_project_and_the_vendored_gpl_file():
    doc = (ROOT / "docs" / "LICENSING.md").read_text(encoding="utf-8")
    missing = []
    for pyproject in _projects():
        name = tomllib.loads((ROOT / pyproject).read_text(encoding="utf-8")).get("project", {}).get("name")
        if name and f"`{name}`" not in doc:
            missing.append(name)
    assert not missing, f"docs/LICENSING.md does not list: {missing}"
    assert "iocost_coef_gen.py" in doc
