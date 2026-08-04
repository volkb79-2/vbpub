"""Per-criterion product-evidence existence-check (CR-12).

Today's gap audit (`effects_carve.py`'s `kind == "gap-audit"` branch) is
entirely prose/LLM-mediated: it asks an agent to re-derive HOLDS/PARTIAL/
ABSENT for every shipped feature's acceptance bullets from scratch, every
time, and never persists a verdict anywhere. CR-12 scoping found the hard
half of formalizing this -- "does a cited invariant still assert the same
claim after being edited" -- has no precedent anywhere in this codebase and
is a genuinely separate, undesigned question (see the CR-12 split ledger
row). This module ships the half that IS buildable today: a citation from a
criterion to a concrete artifact (currently: a pytest node id) either still
resolves or it does not -- the SAME "declared claim vs. machine-computed
reality" shape `product_truth.py`'s `FACT_REGISTRY` already proves out for
whole-project facts, narrowed here to per-criterion evidence.

Additive, not a migration: `2-product-definition.md`'s `acceptance[]` items
may be a plain string (the pre-CR-12a shape, still valid, still the vast
majority of the doc's 18 features) OR a structured object carrying an id,
text, status ("proven" | "absent" -- deliberately not the charter's full
four-state absent/building/proven/degraded vocabulary; the other two states
have no forcing function yet, since nothing has used this mechanism once),
and an evidence citation list. `load_criteria` silently skips plain-string
items -- CR-12a proves the mechanism against a small, real, already-tested
subset (F001, F003) rather than forcing a big-bang retrofit of all 18
features before anything can ship.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter

DEFAULT_PRODUCT_DEFINITION_RELPATH = "nyxloom-trove/2-product-definition.md"


@dataclass(frozen=True)
class Criterion:
    """One STRUCTURED acceptance item: the feature it belongs to, its own
    id/text/status, and the evidence citations a "proven" status makes."""

    feature_id: str
    id: str
    text: str
    status: str
    evidence: tuple[str, ...]


def load_criteria(
    root: Path, doc_relpath: str = DEFAULT_PRODUCT_DEFINITION_RELPATH,
) -> list[Criterion]:
    """Every structured acceptance item in the product-definition doc at
    ``root / doc_relpath``, in file order. A missing doc or one that fails
    to parse yields an empty list rather than raising -- mirrors
    `product_truth.declared_value`'s "absence is a value, not a crash"
    convention. A plain-string acceptance item (unmigrated) is silently
    skipped, not an error: CR-12a does not require every feature to have
    migrated before the mechanism can run.
    """
    path = root / doc_relpath
    if not path.exists():
        return []
    try:
        fm, _body, _line = frontmatter.split_frontmatter(path.read_text(encoding="utf-8"))
    except frontmatter.HandoffParseError:
        return []
    out: list[Criterion] = []
    for feature in fm.get("features") or []:
        if not isinstance(feature, dict):
            continue
        feature_id = feature.get("id", "")
        for item in feature.get("acceptance") or []:
            if not isinstance(item, dict):
                continue  # a plain-string item -- unmigrated, nothing to check
            out.append(Criterion(
                feature_id=feature_id,
                id=item.get("id", ""),
                text=item.get("text", ""),
                status=item.get("status", ""),
                evidence=tuple(item.get("evidence") or ()),
            ))
    return out


def evidence_resolves(root: Path, citation: str) -> bool:
    """Does ``citation`` -- a pytest node id, e.g.
    ``tests/test_x.py::TestFoo::test_bar`` or ``tests/test_x.py::test_bar``,
    or a bare file path with no ``::`` suffix -- still resolve to a real,
    currently-defined test in the tree at ``root``?

    AST-based, not a nested pytest collection run: this program's other
    structural checks (the purity oracle in `tests/test_planning.py`, the
    exception census) are all static analysis over source, never a nested
    pytest invocation from inside a test, and a node id with no parametrize
    brackets is fully resolvable by walking nested class/function
    definitions -- the shape CR-12a's first slice is scoped to (see the
    module docstring; a parametrized node id's ``[...]`` suffix is a real,
    separate case this function does not attempt to resolve, and returns
    False for, rather than silently reporting a false positive).
    """
    file_part, _, rest = citation.partition("::")
    path = root / file_part
    if not path.is_file():
        return False
    if not rest:
        return True
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    scope = tree.body
    for name in rest.split("::"):
        found = None
        for node in scope:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and node.name == name):
                found = node
                break
        if found is None:
            return False
        scope = found.body
    return True
