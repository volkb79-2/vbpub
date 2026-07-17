"""F4a: the spine-doc frontmatter writer. PACKAGE F4a.

A PURE emitter: given already-decided structured content (features,
milestones, backlog items, north-star prose), writes/overwrites the four
direction-spine docs (docs/spine-documents-spec.md) with schema-valid
frontmatter (schemas/spine-*.schema.json, PACKAGE F1).

This module OWNS WRITE AUTHORITY over the spine docs -- unlike F2's
`onboarding._instantiate_spine`, which only ever fills a MISSING doc with a
minimal-valid placeholder and NEVER overwrites an already-present one, this
writer's whole job is to replace that placeholder (or any previously
populated doc) with new structured content, in place. It does not decide
WHAT to write -- that is F4b's guided questionnaire -- and it does not
validate the result against a schema itself: no `lint` import anywhere in
this module (see tests/test_spine_writer.py for the round-trip-through-lint
proof, which lives in the TEST only).

INTERFACE CONTRACT:

- `Feature` / `Milestone` / `BacklogItem` -- one dataclass per spine
  collection entry (spine-product-definition / spine-roadmap /
  spine-backlog schemas). Each has `to_frontmatter() -> dict`, emitting
  ONLY the schema-permitted keys and OMITTING (never null-ing) any optional
  field left at its default `None` -- every optional property in the
  schemas is typed `string`/`integer`/`array`, so a literal YAML `null`
  fails reload (jsonschema rejects `null` against those types; omission is
  the only way to express "not set").
- `write_north_star` / `write_product_definition` / `write_roadmap` /
  `write_backlog(project_root, ...) -> Path` -- each resolves its target
  path via `ProjectConfig.load(project_root).<field>` (trove-relative,
  joined onto `project_root`), falling back to the fixed
  `<project_root>/<trove_name>/<N>-<kind>.md` convention when that config
  key is unset. A project that ran F2's `run_wizard` already has the key
  wired (`onboarding._wire_spine_keys`), so the fallback only matters for a
  project this module is used against directly, bypassing F2. Each
  OVERWRITES the target file in place -- textual shape matches
  `onboarding._minimal_spine_content` exactly:
  `---\\n<yaml frontmatter>---\\n\\n<body>\\n`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ProjectConfig

# (ProjectConfig field / nyxloom.toml [project] key) -> fallback numeric-
# prefixed filename, mirroring onboarding._SPINE_DOC_SPECS's naming.
_FALLBACK_FILENAMES: dict[str, str] = {
    "north_star": "1-north-star.md",
    "product_definition": "2-product-definition.md",
    "roadmap": "3-roadmap.md",
    "backlog": "4-backlog.md",
}


def _resolve_path(project_root: Path, config_key: str, *, trove_name: str) -> Path:
    """The configured trove-relative path for `config_key` (set by
    `onboarding._wire_spine_keys` once a project has onboarded), else the
    fixed `<trove_name>/<N>-<kind>.md` fallback for an un-onboarded project
    this writer is pointed at directly."""
    cfg = ProjectConfig.load(project_root)
    relpath = getattr(cfg, config_key)
    if not relpath:
        relpath = f"{trove_name}/{_FALLBACK_FILENAMES[config_key]}"
    return project_root / relpath


def _write_spine_doc(path: Path, fm: dict, body: str) -> Path:
    fm_text = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm_text}---\n\n{body}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# structured input -- one dataclass per spine collection entry

@dataclass
class Feature:
    """spine-product-definition.schema.json: `features[]` entry."""

    id: str
    title: str
    acceptance: list[str]
    status: str
    milestone: str | None = None

    def to_frontmatter(self) -> dict:
        d: dict = {
            "id": self.id,
            "title": self.title,
            "acceptance": list(self.acceptance),
            "status": self.status,
        }
        if self.milestone is not None:
            d["milestone"] = self.milestone
        return d


@dataclass
class Milestone:
    """spine-roadmap.schema.json: `milestones[]` entry."""

    id: str
    title: str
    target_product_version: int
    features: list[str]
    status: str

    def to_frontmatter(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "target_product_version": self.target_product_version,
            "features": list(self.features),
            "status": self.status,
        }


@dataclass
class BacklogItem:
    """spine-backlog.schema.json: `items[]` entry."""

    id: str
    title: str
    type: str
    component: str | None = None
    context_estimate: str | None = None
    folds_into: str | None = None

    def to_frontmatter(self) -> dict:
        d: dict = {"id": self.id, "title": self.title, "type": self.type}
        if self.component is not None:
            d["component"] = self.component
        if self.context_estimate is not None:
            d["context_estimate"] = self.context_estimate
        if self.folds_into is not None:
            d["folds_into"] = self.folds_into
        return d


# ---------------------------------------------------------------------------
# writers -- one per spine doc, each overwrites its target in place

def write_north_star(
    project_root: Path, *, body: str, trove_name: str = "nyxloom-trove",
) -> Path:
    path = _resolve_path(project_root, "north_star", trove_name=trove_name)
    fm = {"kind": "north-star", "schema_version": 1}
    return _write_spine_doc(path, fm, body)


def write_product_definition(
    project_root: Path,
    *,
    product_version: int,
    features: list[Feature],
    non_goals: list[str] | None = None,
    body: str = "",
    trove_name: str = "nyxloom-trove",
) -> Path:
    path = _resolve_path(project_root, "product_definition", trove_name=trove_name)
    fm: dict = {
        "kind": "product-definition",
        "schema_version": 1,
        "product_version": product_version,
        "features": [f.to_frontmatter() for f in features],
    }
    if non_goals is not None:
        fm["non_goals"] = list(non_goals)
    return _write_spine_doc(path, fm, body)


def write_roadmap(
    project_root: Path,
    *,
    milestones: list[Milestone],
    body: str = "",
    trove_name: str = "nyxloom-trove",
) -> Path:
    path = _resolve_path(project_root, "roadmap", trove_name=trove_name)
    fm = {
        "kind": "roadmap",
        "schema_version": 1,
        "milestones": [m.to_frontmatter() for m in milestones],
    }
    return _write_spine_doc(path, fm, body)


def write_backlog(
    project_root: Path,
    *,
    items: list[BacklogItem],
    body: str = "",
    trove_name: str = "nyxloom-trove",
) -> Path:
    path = _resolve_path(project_root, "backlog", trove_name=trove_name)
    fm = {
        "kind": "backlog",
        "schema_version": 1,
        "items": [i.to_frontmatter() for i in items],
    }
    return _write_spine_doc(path, fm, body)
