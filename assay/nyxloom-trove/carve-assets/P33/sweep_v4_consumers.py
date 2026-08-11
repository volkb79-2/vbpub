#!/usr/bin/env python3
"""Inventory every consumer of a frozen expected-artifact inside the REGISTERED
GATE's transitive execution path. Carver-owned locked asset.

Round 1 found the gate running P26's locked v4 acceptance suite. The repair
retired that invocation and added one scanner pattern for it. Round 2 then found
`gate/python/qualify_topos.py` doing whole-document equality against locked
**P25** v4 templates at the very next gate step. Same disease, one package over.

The lesson, in the round-2 reviewer's words: *a version bump breaks every frozen
expectation any live code compares against, not only the ones that live in a test
file* — and *a defect class closes when its inventory closes, not when its
instance does* (A-141's rule). So this is an INVENTORY, not another pattern.

Method, and why it is shaped this way:

1. Start from `tools/tester-unified-gate.sh` and extract every path it invokes.
   A content scan alone cannot find this class: `qualify_topos.py` builds
   `carve-assets/P25/expected` from `/`-joined components, so no grep for the
   literal path matches it. We therefore look for the *components*.
2. Follow local imports transitively from each invoked Python target, so a helper
   module that owns the comparison is reached even though the gate never names it.
3. For every file in that closure, report each consumption SIGNAL:
     - references a frozen-expectation directory by literal path or by components
     - calls verify_document / verify_text
     - compares a parsed document for equality against a template
     - carries a `schema_version` literal
4. Print a verdict per file: does a v5 shipped schema break it?

Run from the assay project root:
    python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "tools" / "tester-unified-gate.sh"

# Directories holding frozen expected artifacts. Components, not just paths,
# because the class hides behind path joining.
FROZEN_DIR_COMPONENTS = ("carve-assets", "expected", "fixtures", "verdicts")

SIGNALS = {
    "verify_document": re.compile(r"\bverify_document\b"),
    "verify_text": re.compile(r"\bverify_text\b"),
    "schema_version_literal": re.compile(r"schema_version"),
    "document_equality": re.compile(
        r"(!=\s*expected|==\s*expected|expected\s*!=|expected\s*==|"
        r"normalized\s*!=|assert\s+actual\s*==)"
    ),
    "reads_expected_template": re.compile(
        r"(_EXPECTED_ROOT|expected[\"'\]/]|template\.read_text|"
        r"_substitute_template)"
    ),
}


def gate_invocations() -> list[Path]:
    """Every repository path the gate script hands to an interpreter."""
    text = GATE.read_text()
    found: list[Path] = []
    for m in re.finditer(r'\$worktree/assay/([A-Za-z0-9_./-]+\.py)', text):
        found.append(ROOT / m.group(1))
    # pytest targets given relative to the project root
    for m in re.finditer(r'-m pytest\s+(?:"[^"]*"|)?\s*([A-Za-z0-9_./-]+\.py)', text):
        p = ROOT / m.group(1)
        if p.exists():
            found.append(p)
    for m in re.finditer(r'-m pytest\s+(tests)\b', text):
        found.append(ROOT / "tests")
    return sorted(set(found))


def local_imports(path: Path) -> set[Path]:
    """Sibling/relative modules this file imports, so helpers are reached."""
    out: set[Path] = set()
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return out
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            leaf = name.split(".")[-1]
            for cand in (path.parent / f"{leaf}.py", ROOT / "src" / "assay" / f"{leaf}.py"):
                if cand.exists() and cand != path:
                    out.add(cand)
    return out


def closure(seeds: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    queue = list(seeds)
    while queue:
        item = queue.pop()
        if item in seen:
            continue
        seen.add(item)
        if item.is_dir():
            for child in sorted(item.rglob("*.py")):
                if child not in seen:
                    queue.append(child)
            continue
        for dep in local_imports(item):
            if dep not in seen:
                queue.append(dep)
    return sorted(p for p in seen if p.is_file())


def frozen_dir_hits(text: str) -> list[str]:
    hits = []
    for comp in FROZEN_DIR_COMPONENTS:
        if f'"{comp}"' in text or f"'{comp}'" in text or f"/{comp}/" in text:
            hits.append(comp)
    return hits


def main() -> int:
    seeds = gate_invocations()
    print("=== gate invocations (seeds) ===")
    for s in seeds:
        print("   ", s.relative_to(ROOT))

    files = closure(seeds)
    print(f"\n=== transitive closure: {len(files)} python files ===")

    consumers = []
    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue
        sig = {k: bool(r.search(text)) for k, r in SIGNALS.items()}
        dirs = frozen_dir_hits(text)
        # A consumer of a frozen expectation: it names a frozen dir AND either
        # verifies or compares documents.
        is_consumer = bool(dirs) and (
            sig["verify_document"] or sig["verify_text"] or sig["document_equality"]
            or sig["reads_expected_template"]
        )
        if is_consumer:
            consumers.append((f, dirs, sig))

    print(f"\n=== CONSUMERS OF A FROZEN EXPECTED ARTIFACT: {len(consumers)} ===")
    for f, dirs, sig in consumers:
        rel = f.relative_to(ROOT)
        active = ",".join(k for k, v in sig.items() if v)
        print(f"\n  {rel}")
        print(f"      frozen-dir components : {dirs}")
        print(f"      signals               : {active}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
