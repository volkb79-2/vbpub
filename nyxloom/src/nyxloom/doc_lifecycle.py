"""Document lifecycle/archive model (CR-01, DR-04).

A project's product-truth documents ([refs] in nyxloom.toml, plus any path a
handoff or the daemon names) come in exactly one of two states:

- **active** — the current authority for its concern; may be referenced
  anywhere.
- **archived** — superseded or historical; lives under `docs/archive/` and
  MUST NOT be referenced by an active `[refs]` entry, a handoff `source.ref`,
  or the daemon's default carve-context assembly. Explicit opt-in access (a
  separate, clearly-named config surface) may still read it.

`is_archived` is a pure PATH-CONTAINMENT check (resolves symlinks and `..`
normalization, never reads file content) so a caller deciding whether to
exclude a path from a default packet never has to open the archived file to
make that decision — fail-closed by construction (PACKAGE CR-01 contract
item 6): an unreadable or missing target under the archive root is still
excluded, because containment alone decides it.

`read_lifecycle` is a separate, best-effort content read used ONLY to enrich
diagnostic messages (lint); its failure never flips an exclusion decision.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path

import jsonschema

from . import frontmatter
from .config import ProjectConfig
from .types import LintFinding

ARCHIVE_RELPATH = "docs/archive"
PRODUCT_DOCS_RELPATH = "docs/archive/product-docs"

_SCHEMA_FILE = "doc-lifecycle.schema.json"


def archive_root(root: Path) -> Path:
    """The project's archive root (docs/archive/), resolved."""
    return (root / ARCHIVE_RELPATH).resolve()


def product_docs_root(root: Path) -> Path:
    """The subset of the archive holding lifecycle-tracked product docs
    (as opposed to unrelated pre-existing free-form archive content, e.g.
    docs/archive/sessions/, which carries no lifecycle contract)."""
    return (root / PRODUCT_DOCS_RELPATH).resolve()


def is_archived(root: Path, target: Path) -> bool:
    """True iff `target` resolves (symlinks + normalization) inside the
    project's docs/archive/ tree. `root` and `target` may be relative or
    absolute; both are resolved before the containment test so a `../`
    escape or a symlink pointing into the archive is caught identically to
    a direct path (CR-01 contract item 6)."""
    resolved_archive = archive_root(root)
    resolved_target = target if target.is_absolute() else (root / target)
    resolved_target = resolved_target.resolve()
    try:
        resolved_target.relative_to(resolved_archive)
    except ValueError:
        return False
    return True


def _schema() -> dict:
    text = importlib.resources.files("nyxloom.schemas").joinpath(_SCHEMA_FILE).read_text(encoding="utf-8")
    return json.loads(text)


def read_lifecycle(path: Path) -> dict | None:
    """Best-effort frontmatter read for an archived doc, for DIAGNOSTIC
    messages only. Returns None on any parse failure -- callers must not
    treat None as "not archived" (containment, not this, decides that)."""
    try:
        text = path.read_text(encoding="utf-8")
        fm, _body, _line = frontmatter.split_frontmatter(text)
    except (OSError, frontmatter.HandoffParseError):
        return None
    return fm


def describe(path: Path) -> str:
    """Human-readable one-line reason for an archived path, for lint
    messages. Falls back to a generic label when metadata is missing or
    unreadable -- the exclusion itself never depends on this succeeding."""
    fm = read_lifecycle(path)
    if not fm:
        return "archived document (lifecycle metadata unreadable)"
    status = fm.get("status")
    if status == "superseded":
        return f"superseded by {fm.get('superseded_by', '?')}"
    if status == "historical":
        return "historical (no current successor)"
    return "archived document"


def validate_lifecycle_frontmatter(fm: dict) -> list[str]:
    """Schema-validate an archived doc's frontmatter against
    doc-lifecycle.schema.json. Returns a list of human-readable error
    strings (empty when valid)."""
    validator = jsonschema.Draft202012Validator(_schema())
    errors = sorted(validator.iter_errors(fm), key=lambda e: list(e.absolute_path))
    out = []
    for error in errors:
        json_path = ".".join(str(p) for p in error.absolute_path) or "$"
        out.append(f"{json_path}: {error.message}")
    return out


def iter_product_docs(root: Path) -> list[Path]:
    """Every .md file under docs/archive/product-docs/ (ARC1's scan set),
    sorted for deterministic reporting."""
    base = product_docs_root(root)
    if not base.exists():
        return []
    return sorted(base.rglob("*.md"))


def lint_archive(cfg: ProjectConfig) -> dict[str, list[LintFinding]]:
    """relpath -> findings for every doc under docs/archive/product-docs/
    (ARC1): frontmatter must parse and schema-validate against
    doc-lifecycle.schema.json -- fail-closed, mirroring lint.py's S4 (an
    unparsable or non-conforming archived doc is a hard error, never a
    silent skip, because a mis-tagged archive entry could otherwise pass
    review-by-omission)."""
    results: dict[str, list[LintFinding]] = {}
    root_resolved = cfg.root.resolve()
    for path in iter_product_docs(cfg.root):
        rel = str(path.resolve().relative_to(root_resolved))
        try:
            text = path.read_text(encoding="utf-8")
            fm, _body, _line = frontmatter.split_frontmatter(text)
        except (OSError, frontmatter.HandoffParseError) as e:
            msg = getattr(e, "errors", None)
            detail = "; ".join(msg) if msg else str(e)
            results[rel] = [LintFinding(
                rule="ARC1", severity="error",
                message=f"unparsable lifecycle frontmatter: {detail}",
                path=rel,
            )]
            continue
        errors = validate_lifecycle_frontmatter(fm)
        if errors:
            results[rel] = [
                LintFinding(rule="ARC1", severity="error", message=err, path=rel)
                for err in errors
            ]
    return results
