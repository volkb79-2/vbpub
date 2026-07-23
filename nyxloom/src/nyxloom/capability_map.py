"""Model capability catalog (D-R13, docs/routing-model-redesign.md:253-296).

The capability half bolted onto `free_models.py`'s discovery half:
`benchmark_sources.fetch_all()` feeds source-native `BenchmarkRecord`s in
here, this module turns them into a persisted, operator-tunable
`CapabilityRecord` catalog -- per-axis capability bands plus role-gating
(`may_review`/`may_carve`) -- and writes it to routes.toml as a sibling
managed block, exactly like `free_models.write_routes_toml`. `config.py`
stays FROZEN CORE: a fresh top-level `[capability_catalog]` block and a
`[capability_map]` config table are simply ignored by `config.Routes.load`
(it only reads `data.get("tiers")` / `data.get("routes")`), so there is no
collision and no config.py edit.

INTERFACE CONTRACT:

- `CapabilityRecord` -- one catalog entry: model_id/source, the three-axis
  `scores` (a SUBSET may be present -- a benchmark source need not rate
  every axis), price/context_length carried through from the benchmark
  record, per-axis `bands` (ALWAYS all three axes -- an axis absent from
  `scores` bands as 0/unrated, same convention as
  `band_thresholds.assign_band(None, ...)`), `may_review`/`may_carve`, and
  `raw` (the source's native record, for the dashboard). Axis vocabulary is
  EXACTLY `intelligence`, `coding`, `agentic` (see `AXES`) -- the design's
  "reasoning" axis IS `intelligence`, there is no separate "reasoning" key.
- `CapabilityMapConfig` -- this module's OWN `[capability_map]` routes.toml
  loader (mirrors `benchmark_sources.BenchmarkConfig.load`: reads straight
  from `paths.routes_path()` via `tomllib`, NOT through frozen `config.py`).
  `role_gating`: `"auto"` bands drive `may_review`/`may_carve` directly
  (`agentic` band >= `review_min_band`/`carve_min_band`); `"operator"`
  (default) makes role eligibility an explicit operator allow-list
  (`review_grants`/`carve_grants` by `model_id`) -- bands are still
  computed and carried either way, only ROLE AUTHORITY differs (the D-R13
  "role-eligibility operator-confirmed by default" hybrid). Optional hard
  filters (`min_context`, `required_flags` looked up in `record.raw`)
  EXCLUDE a record from the catalog entirely -- "hard-exclude a route...
  regardless of score" per D-R13, not a soft ineligible-flag.
- `assemble_catalog(records, cfg)` -- the core: band every axis via
  `band_thresholds.assign_bands`, apply hard filters, compute
  `may_review`/`may_carve` per `cfg.role_gating`.
- `write_capability_catalog(path, records)` -- mirrors
  `free_models.write_routes_toml`'s strip-then-append idempotence with this
  module's OWN distinct managed-block markers (`_MANAGED_BEGIN`/
  `_MANAGED_END` below): writing twice is byte-identical, a pre-existing
  free-models managed block or hand-authored `[tiers.*]`/`[routes.*]` table
  is left byte-identical (this module never touches any table but its own
  managed block), and the result is valid TOML `tomllib`/`config.Routes.
  load` parse without error. `raw` is persisted as a `raw_json` JSON string
  field (not a nested TOML table) so an arbitrary source-native record --
  which may contain `None`/deeply-nested values TOML cannot represent
  directly -- always round-trips through a valid file; the in-memory
  `CapabilityRecord.raw` keeps the real dict for the dashboard (B19).
- `refresh_catalog(cfg=None, sources=None)` -- thin convenience:
  `benchmark_sources.fetch_all(sources)` + `assemble_catalog(...)`, one
  entry point for a future scheduled-job/CLI verb (B20) and dashboard
  (B19). No daemon/CLI wiring here -- out of scope for this module.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import benchmark_sources, paths
from .band_thresholds import AxisThresholds, DEFAULT_CUTOFFS, assign_bands
from .benchmark_sources import BenchmarkRecord
from .log import get_logger

log = get_logger("capability_map")

AXES: tuple[str, ...] = ("intelligence", "coding", "agentic")
"""The fixed three-axis capability vocabulary (D-R13). Never add a fourth
axis or rename one of these -- "reasoning" IS "intelligence"."""


# ---------------------------------------------------------------------------
# catalog record

@dataclass(frozen=True)
class CapabilityRecord:
    """One capability-catalog entry -- a benchmark record extended with
    per-axis bands and operator/auto role-gating decisions."""

    model_id: str
    source: str
    scores: dict[str, float]
    price_input: float | None
    price_output: float | None
    context_length: int | None
    bands: dict[str, int]
    may_review: bool
    may_carve: bool
    raw: dict


# ---------------------------------------------------------------------------
# [capability_map] routes.toml configuration

@dataclass
class CapabilityMapConfig:
    """Optional ``[capability_map]`` routes.toml configuration."""

    role_gating: str = "operator"                       # "auto" | "operator"
    cutoffs: dict[str, list[float]] = field(
        default_factory=lambda: {axis: list(vals) for axis, vals in DEFAULT_CUTOFFS.items()})
    review_min_band: int = 2
    carve_min_band: int = 3
    review_grants: list[str] = field(default_factory=list)
    carve_grants: list[str] = field(default_factory=list)
    min_context: int | None = None
    required_flags: list[str] = field(default_factory=list)

    @classmethod
    def default(cls) -> "CapabilityMapConfig":
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityMapConfig":
        cutoffs = {axis: list(vals) for axis, vals in DEFAULT_CUTOFFS.items()}
        for axis, vals in dict(data.get("cutoffs", {})).items():
            cutoffs[axis] = [float(v) for v in vals]
        min_context = data.get("min_context")
        return cls(
            role_gating=str(data.get("role_gating", "operator")),
            cutoffs=cutoffs,
            review_min_band=int(data.get("review_min_band", 2)),
            carve_min_band=int(data.get("carve_min_band", 3)),
            review_grants=list(data.get("review_grants", [])),
            carve_grants=list(data.get("carve_grants", [])),
            min_context=(int(min_context) if min_context is not None else None),
            required_flags=list(data.get("required_flags", [])),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "CapabilityMapConfig":
        p = path or paths.routes_path()
        if not p.exists():
            log.debug("capability_map config resolved", present=False)
            return cls.default()
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        cfg = cls.from_dict(data.get("capability_map", {}))
        log.debug("capability_map config resolved", present=True,
                  role_gating=cfg.role_gating)
        return cfg

    def thresholds(self) -> AxisThresholds:
        return AxisThresholds(cutoffs=self.cutoffs)


def load_capability_catalog(path: Path | None = None) -> list[CapabilityRecord]:
    """Load this module's persisted capability catalog, if one exists."""
    p = path or paths.routes_path()
    if not p.exists():
        return []
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    catalog = data.get("capability_catalog")
    if catalog is None:
        return []
    rows = catalog.get("records", [])
    if not rows:
        return []
    return [
        CapabilityRecord(
            model_id=row["model_id"],
            source=row["source"],
            scores=dict(row.get("scores", {})),
            price_input=row.get("price_input"),
            price_output=row.get("price_output"),
            context_length=row.get("context_length"),
            bands=dict(row.get("bands", {})),
            may_review=row.get("may_review", False),
            may_carve=row.get("may_carve", False),
            raw=json.loads(row["raw_json"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# catalog assembly

def _passes_hard_filters(record: BenchmarkRecord, cfg: CapabilityMapConfig) -> bool:
    """Hard-exclude (D-R13): context_length/capability-flag gaps drop a
    record from the catalog entirely, regardless of its scores."""
    if cfg.min_context is not None:
        if record.context_length is None or record.context_length < cfg.min_context:
            return False
    for flag in cfg.required_flags:
        if not record.raw.get(flag):
            return False
    return True


def assemble_catalog(records: list[BenchmarkRecord],
                     cfg: CapabilityMapConfig) -> list[CapabilityRecord]:
    """Band every axis, apply hard filters, and resolve role-gating for
    each benchmark record -- the core of the D-R13 capability catalog."""
    thresholds = cfg.thresholds()
    out: list[CapabilityRecord] = []
    for record in records:
        if not _passes_hard_filters(record, cfg):
            continue
        bands = assign_bands(record.scores, thresholds)
        for axis in AXES:
            bands.setdefault(axis, 0)
        agentic_band = bands["agentic"]
        if cfg.role_gating == "auto":
            may_review = agentic_band >= cfg.review_min_band
            may_carve = agentic_band >= cfg.carve_min_band
        else:
            may_review = record.model_id in cfg.review_grants
            may_carve = record.model_id in cfg.carve_grants
        out.append(CapabilityRecord(
            model_id=record.model_id,
            source=record.source,
            scores=dict(record.scores),
            price_input=record.price_input,
            price_output=record.price_output,
            context_length=record.context_length,
            bands=bands,
            may_review=may_review,
            may_carve=may_carve,
            raw=record.raw,
        ))
    return out


# ---------------------------------------------------------------------------
# routes.toml writer (managed block) -- mirrors free_models.write_routes_toml
# with this module's OWN distinct markers; never touches any other table.

_MANAGED_BEGIN = ("# === nyxloom-capability-map: BEGIN (auto-generated by "
                  "`nyxloom capability-map refresh`; hand edits here are lost "
                  "on the next refresh) ===\n")
_MANAGED_END = "# === nyxloom-capability-map: END ===\n"


def _strip_managed_block(lines: list[str]) -> list[str]:
    begin_idx = next((i for i, l in enumerate(lines) if l == _MANAGED_BEGIN), None)
    if begin_idx is None:
        return lines
    end_idx = next((i for i in range(begin_idx, len(lines)) if lines[i] == _MANAGED_END),
                    len(lines) - 1)
    return lines[:begin_idx] + lines[end_idx + 1:]


def _toml_str(value: str) -> str:
    """Render `value` as a TOML basic string. JSON and TOML basic-string
    escaping rules coincide (backslash, double-quote, control chars) for
    every character this module ever emits, so `json.dumps` doubles as a
    TOML string encoder without a second hand-rolled escaper."""
    return json.dumps(value)


def _render_record_block(rec: CapabilityRecord) -> str:
    lines = ["[[capability_catalog.records]]\n"]
    lines.append(f"model_id = {_toml_str(rec.model_id)}\n")
    lines.append(f"source = {_toml_str(rec.source)}\n")
    scores = ", ".join(f"{axis} = {rec.scores[axis]}" for axis in sorted(rec.scores))
    lines.append(f"scores = {{{scores}}}\n")
    if rec.price_input is not None:
        lines.append(f"price_input = {rec.price_input}\n")
    if rec.price_output is not None:
        lines.append(f"price_output = {rec.price_output}\n")
    if rec.context_length is not None:
        lines.append(f"context_length = {rec.context_length}\n")
    bands = ", ".join(f"{axis} = {rec.bands[axis]}" for axis in sorted(rec.bands))
    lines.append(f"bands = {{{bands}}}\n")
    lines.append(f"may_review = {str(rec.may_review).lower()}\n")
    lines.append(f"may_carve = {str(rec.may_carve).lower()}\n")
    lines.append(f"raw_json = {_toml_str(json.dumps(rec.raw, sort_keys=True))}\n")
    lines.append("\n")
    return "".join(lines)


def _render_managed_block(records: list[CapabilityRecord]) -> str:
    if not records:
        return "".join([_MANAGED_BEGIN, "[capability_catalog]\n", "records = []\n",
                        "\n", _MANAGED_END])
    parts = [_MANAGED_BEGIN]
    for rec in records:
        parts.append(_render_record_block(rec))
    parts.append(_MANAGED_END)
    return "".join(parts)


def write_capability_catalog(path: Path, records: list[CapabilityRecord]) -> None:
    """Rewrite `path` (routes.toml): drop any pre-existing capability-map
    managed block (this module's own markers only), then append a fresh
    one. Every other table -- hand-authored or another module's managed
    block (e.g. free_models' `[tiers.free-high]`) -- is left byte-
    identical."""
    text = path.read_text(encoding="utf-8") if path.exists() else 'revision = "unversioned"\n'
    lines = text.splitlines(keepends=True)
    lines = _strip_managed_block(lines)
    # Drop trailing blank lines left behind by a prior strip (the blank
    # separator line this function itself appends before the managed
    # block) -- otherwise every write/strip/rewrite cycle grows the gap by
    # one more blank line, breaking the byte-identical idempotence
    # contract.
    while lines and lines[-1] == "\n":
        lines.pop()
    new_text = "".join(lines)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    new_text += "\n" + _render_managed_block(list(records))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# convenience entry point (B19 dashboard / B20 scheduled-job call this)

def refresh_catalog(cfg: CapabilityMapConfig | None = None,
                    sources: Any = None) -> tuple[list[CapabilityRecord], dict[str, str]]:
    """Fetch every enabled benchmark source and assemble the capability
    catalog in one call. Returns `(catalog, source_errors)` -- a source
    that failed to fetch never aborts the others (see
    `benchmark_sources.fetch_all`). Does NOT write anything; call
    `write_capability_catalog` with the result when a persisted refresh is
    wanted."""
    resolved_cfg = cfg or CapabilityMapConfig.load()
    records, errors = benchmark_sources.fetch_all(sources)
    catalog = assemble_catalog(records, resolved_cfg)
    return catalog, errors
