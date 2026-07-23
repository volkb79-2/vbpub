"""Pluggable free-model discovery + `[tiers.free-high]` routes.toml refresh.

Implements D-R12 (docs/routing-model-redesign.md): today's `[tiers.free-high]`
block in routes.toml is hand-curated ("Vetted free OpenRouter coding models");
this module DISCOVERS currently-free models across multiple providers at
runtime and regenerates that block. It does NOT touch model
SELECTION/scoring (D-R5), per-project policy (D-R6), or live route probing
(D-R9) -- those are separate, out-of-scope backlog items (B1/B2/B3).

INTERFACE CONTRACT:

- `DiscoveredModel` -- one model a source found, tagged with everything a
  route-writer needs (id, source, base_url, context_length, requires_key/
  key_env, privacy, free).
- `FreeModelSource` (ABC) -- `discover() -> list[DiscoveredModel]`. Every
  source is constructed from a `SourceConfig` row.
- `SOURCE KIND REGISTRY` (`register_kind` decorator -> `_SOURCE_KINDS`) is
  the pluggability point. **Extending with a new provider:**
    * If it exposes a GET `{base_url}/models` OpenAI-compatible listing and
      its whole catalog counts as free -- add ONE `[free_models.sources.
      <name>]` TOML row (kind="openai-compat", base_url, key_env, privacy)
      to routes.toml, or one row to `DEFAULT_SOURCES` below. Zero new code.
    * Otherwise (a genuinely different response shape, e.g. OpenRouter's
      per-model pricing dict) -- write one small `FreeModelSource` subclass,
      decorate it `@register_kind("<kind-name>")`, and reference that kind
      from a `SourceConfig` row. No other module changes.
- `FreeModelsConfig.load()` reads the OPTIONAL `[free_models]` / `[free_
  models.sources.<name>]` tables from routes.toml (`paths.routes_path()`,
  the same file `config.Routes.load()` reads -- "parsed alongside"), merged
  over `DEFAULT_SOURCES` (an explicit TOML row overrides/extends a built-in
  by name; an unconfigured deployment gets `DEFAULT_SOURCES` verbatim). A
  missing/absent `[free_models]` table is not an error -- `default()` is
  used.
- `discover_all(cfg, only=None)` calls every ENABLED source's `discover()`,
  in isolation: a source that raises (bad JSON, timeout, non-200 via
  urllib's own HTTPError, network failure) is caught, logged as a WARNING,
  and skipped -- it never aborts the other sources' discovery. A source
  whose `key_env` is configured but unset in the environment is skipped by
  the source itself (a DEBUG log, not a warning -- this is an expected,
  common "provider not opted into" state, not a failure).
- `refresh(cfg, only=None, dry_run=False, path=None) -> RefreshResult`
  aggregates every discovered FREE model (deduped by (source, id), sorted
  for determinism) and writes them into routes.toml as `[routes.auto-
  <source>-<slug>]` definitions + a regenerated `[tiers.free-high]` ordered
  list, INSIDE a single delimited "managed block" appended at end-of-file
  (see `write_routes_toml`). `dry_run=True` computes the exact same
  `RefreshResult` but performs no write.

WRITER SAFETY (`write_routes_toml`): `config.update_routes` (routes.toml's
existing FROZEN-CORE writer) only rewrites an existing tier's `routes =
[...]` line -- it cannot emit brand-new `[routes.*]` table definitions, and
`config.py` is frozen core (implementation agents must not modify it -- see
`nyxloom/__init__.py`). This module is therefore a SIBLING writer, not an
extension of `update_routes`. It:
  1. Removes any PRE-EXISTING `[tiers.free-high]` table (hand-authored or
     from a prior refresh) -- TOML forbids redefining a table, so the old
     one must go before a fresh one is appended.
  2. Removes any pre-existing managed block (see the BEGIN/END markers).
  3. Appends a fresh managed block: `[tiers.free-high]` + `routes = [...]`
     + one `[routes.auto-*]` table per discovered free model.
Every OTHER tier, and every OTHER `[routes.*]` definition (including the
hand-curated `openrouter-free-*` routes already in routes.host.toml) is
left byte-identical -- they simply become unreferenced by the tier once a
refresh runs, never deleted or rewritten. This satisfies "must not clobber
non-free tiers or hand-authored routes" without needing surgical line-level
edits into an arbitrarily-interspersed hand-authored region.

Every generated route carries `prompt_hints = ["free-endpoint"]` (the same
key `adapters.build_dispatch`'s no-secrets confidentiality guard scans for)
and dispatches via `opencode` using the `<source>/<model-id>` addressing
convention the existing hand-curated `openrouter-free-*` routes already use
(`openrouter/nvidia/...:free`) -- generalized here to `<source-name>/<model-
id>` for any other provider (e.g. `groq/llama-3.3-70b-versatile`).
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import paths
from .log import get_logger

log = get_logger("free_models")

_HTTP_TIMEOUT = 10.0     # seconds -- discovery must never hang a refresh/tick

Privacy = Literal["private", "may-train", "unknown"]


# ---------------------------------------------------------------------------
# discovered model + source config

@dataclass
class DiscoveredModel:
    id: str                          # provider-native model id (e.g. "nvidia/nemotron-3-ultra-550b-a55b:free")
    source: str                      # source name (e.g. "openrouter", "groq")
    base_url: str
    context_length: int | None
    requires_key: bool
    key_env: str | None
    privacy: Privacy
    free: bool


@dataclass
class SourceConfig:
    name: str
    kind: str                        # dispatch key into _SOURCE_KINDS
    enabled: bool = True
    base_url: str | None = None
    key_env: str | None = None
    privacy: Privacy = "unknown"
    all_free: bool = False           # whole catalog counts as free (openai-compat sources)


# ---------------------------------------------------------------------------
# pluggable source base + kind registry

class FreeModelSource(ABC):
    """Base for a pluggable free-model discovery source. Subclass +
    `@register_kind(...)` for a genuinely new response shape; most new
    providers instead need zero code -- see module docstring."""

    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg
        self.name = cfg.name

    @abstractmethod
    def discover(self) -> list[DiscoveredModel]:
        ...


_SOURCE_KINDS: dict[str, type[FreeModelSource]] = {}


def register_kind(kind: str):
    """Class decorator: register a FreeModelSource subclass under `kind` so
    a SourceConfig row naming that kind can be instantiated by
    `build_sources`/`discover_all`. THE pluggability point (module
    docstring)."""
    def _deco(cls: type[FreeModelSource]) -> type[FreeModelSource]:
        _SOURCE_KINDS[kind] = cls
        return cls
    return _deco


def _fetch_json(url: str, *, key_env: str | None = None,
                 timeout: float = _HTTP_TIMEOUT) -> Any:
    """Sole network entry point for this module (stdlib urllib -- the
    codebase already uses it for ntfy/webhooks in notify.py/commands.py, so
    this adds no new dependency). Raises on any non-2xx / network / JSON
    failure; callers (discover_all) are responsible for per-source
    isolation -- this function itself never swallows an error."""
    headers = {"Accept": "application/json"}
    if key_env:
        key = os.environ.get(key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body)


# ---------------------------------------------------------------------------
# built-in sources

_OPENROUTER_DEFAULT_URL = "https://openrouter.ai/api/v1/models"


@register_kind("openrouter")
class OpenRouterSource(FreeModelSource):
    """OpenRouter's public `GET /api/v1/models` listing -- no auth required
    to LIST (the free predicate: `pricing.prompt == "0" AND pricing.
    completion == "0"`, equivalently an id ending `:free`). `requires_key`/
    `key_env` on the resulting DiscoveredModel describe what INFERENCE
    through the model needs (OPENROUTER_API_KEY), not the listing call."""

    def discover(self) -> list[DiscoveredModel]:
        url = self.cfg.base_url or _OPENROUTER_DEFAULT_URL
        data = _fetch_json(url, timeout=_HTTP_TIMEOUT)
        out: list[DiscoveredModel] = []
        for entry in data.get("data", []):
            model_id = entry.get("id")
            if not model_id:
                continue
            pricing = entry.get("pricing") or {}
            is_free = (
                str(model_id).endswith(":free")
                or (str(pricing.get("prompt", "")) == "0"
                    and str(pricing.get("completion", "")) == "0")
            )
            if not is_free:
                continue
            out.append(DiscoveredModel(
                id=model_id,
                source=self.name,
                base_url=url,
                context_length=entry.get("context_length"),
                requires_key=True,
                key_env=self.cfg.key_env or "OPENROUTER_API_KEY",
                privacy="may-train",
                free=True,
            ))
        return out


@register_kind("openai-compat")
class OpenAICompatSource(FreeModelSource):
    """Generic OpenAI-compatible `GET {base_url}/models` source for a
    provider whose whole catalog is treated as free (`cfg.all_free`). A new
    provider of this shape needs only ONE `SourceConfig`/TOML row -- no new
    class. Skips cleanly (returns `[]`, logged at DEBUG) when `cfg.key_env`
    is set but absent from the environment -- an unconfigured provider is
    not an error."""

    def discover(self) -> list[DiscoveredModel]:
        if self.cfg.key_env and not os.environ.get(self.cfg.key_env):
            log.debug("free-model source skipped: key env not set",
                      source=self.name, key_env=self.cfg.key_env)
            return []
        if not self.cfg.base_url:
            log.warning("free-model source misconfigured: no base_url", source=self.name)
            return []
        url = f"{self.cfg.base_url.rstrip('/')}/models"
        data = _fetch_json(url, key_env=self.cfg.key_env, timeout=_HTTP_TIMEOUT)
        out: list[DiscoveredModel] = []
        for entry in data.get("data", []):
            model_id = entry.get("id")
            if not model_id:
                continue
            out.append(DiscoveredModel(
                id=model_id,
                source=self.name,
                base_url=self.cfg.base_url,
                context_length=entry.get("context_length"),
                requires_key=True,
                key_env=self.cfg.key_env,
                privacy=self.cfg.privacy,
                free=self.cfg.all_free,
            ))
        return out


# Sane default: OpenRouter enabled outright (public listing, no key needed
# to discover); the Tier-2 "whole free tier" providers are listed too but
# each is a no-op (discover() returns []) until its key_env is exported --
# see OpenAICompatSource above. Enabling/disabling one is a one-line edit
# (or a routes.toml [free_models.sources.<name>] `enabled = false` override).
DEFAULT_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(name="openrouter", kind="openrouter", enabled=True,
                 base_url=_OPENROUTER_DEFAULT_URL,
                 key_env="OPENROUTER_API_KEY", privacy="may-train"),
    SourceConfig(name="groq", kind="openai-compat", enabled=True,
                 base_url="https://api.groq.com/openai/v1",
                 key_env="GROQ_API_KEY", privacy="private", all_free=True),
    SourceConfig(name="cerebras", kind="openai-compat", enabled=True,
                 base_url="https://api.cerebras.ai/v1",
                 key_env="CEREBRAS_API_KEY", privacy="private", all_free=True),
    SourceConfig(name="gemini", kind="openai-compat", enabled=True,
                 base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                 key_env="GEMINI_API_KEY", privacy="may-train", all_free=True),
    SourceConfig(name="mistral", kind="openai-compat", enabled=True,
                 base_url="https://api.mistral.ai/v1",
                 key_env="MISTRAL_API_KEY", privacy="may-train", all_free=True),
    SourceConfig(name="sambanova", kind="openai-compat", enabled=True,
                 base_url="https://api.sambanova.ai/v1",
                 key_env="SAMBANOVA_API_KEY", privacy="private", all_free=True),
)


# ---------------------------------------------------------------------------
# [free_models] config table (routes.toml)

@dataclass
class FreeModelsConfig:
    sources: dict[str, SourceConfig] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "FreeModelsConfig":
        return cls(sources={s.name: s for s in DEFAULT_SOURCES})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FreeModelsConfig":
        sources = {s.name: s for s in DEFAULT_SOURCES}
        for name, row in dict(data.get("sources", {})).items():
            base = sources.get(name)
            kind = row.get("kind", base.kind if base else None)
            if not kind:
                raise ValueError(
                    f"free_models.sources.{name}: 'kind' is required for a "
                    "new (non-default) source")
            sources[name] = SourceConfig(
                name=name,
                kind=kind,
                enabled=bool(row.get("enabled", base.enabled if base else True)),
                base_url=row.get("base_url", base.base_url if base else None),
                key_env=row.get("key_env", base.key_env if base else None),
                privacy=row.get("privacy", base.privacy if base else "unknown"),
                all_free=bool(row.get("all_free", base.all_free if base else False)),
            )
        return cls(sources=sources)

    @classmethod
    def load(cls, path: Path | None = None) -> "FreeModelsConfig":
        p = path or paths.routes_path()
        if not p.exists():
            log.debug("free-models config resolved", present=False)
            return cls.default()
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        cfg = cls.from_dict(data.get("free_models", {}))
        log.debug("free-models config resolved", present=True,
                  source_count=len(cfg.sources))
        return cfg


# ---------------------------------------------------------------------------
# discovery aggregation

def discover_all(cfg: FreeModelsConfig | None = None, *,
                  only: str | None = None) -> list[DiscoveredModel]:
    """Run every ENABLED source's discover(), isolated: one source raising
    (timeout, non-200, bad JSON, ...) is logged as a WARNING and skipped --
    it never aborts discovery for the other sources. `only` restricts to a
    single source name (CLI `--source`)."""
    cfg = cfg or FreeModelsConfig.load()
    results: list[DiscoveredModel] = []
    for name, sc in cfg.sources.items():
        if only and name != only:
            continue
        if not sc.enabled:
            log.debug("free-model source disabled", source=name)
            continue
        kind_cls = _SOURCE_KINDS.get(sc.kind)
        if kind_cls is None:
            log.warning("free-model source: unknown kind", source=name, kind=sc.kind)
            continue
        source = kind_cls(sc)
        try:
            found = source.discover()
        except Exception as exc:
            log.warning("free-model source failed", source=name, detail=str(exc))
            continue
        log.debug("free-model source discovered", source=name, count=len(found))
        results.extend(found)
    return results


# ---------------------------------------------------------------------------
# routes.toml writer (managed block)

FREE_TIER_NAME = "free-high"
_MANAGED_BEGIN = ("# === nyxloom-free-models: BEGIN (auto-generated by "
                  "`nyxloom free-models refresh`; hand edits here are lost "
                  "on the next refresh) ===\n")
_MANAGED_END = "# === nyxloom-free-models: END ===\n"


def _locate_table(lines: list[str], header: str) -> tuple[int, int] | None:
    """Find `header` (an exact `[...]`/`[tiers.x]` line) and the end index
    (exclusive) of its body -- the next `[...]` header line, or EOF. Same
    section-scan convention as config.update_routes/update_project_policy."""
    start = None
    for i, raw in enumerate(lines):
        if raw.strip() == header:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        s = lines[i].strip()
        if s.startswith("[") and s.endswith("]"):
            end = i
            break
    return start, end


def _strip_table(lines: list[str], header: str) -> list[str]:
    loc = _locate_table(lines, header)
    if loc is None:
        return lines
    start, end = loc
    return lines[:start] + lines[end:]


def _strip_managed_block(lines: list[str]) -> list[str]:
    begin_idx = next((i for i, l in enumerate(lines) if l == _MANAGED_BEGIN), None)
    if begin_idx is None:
        return lines
    end_idx = next((i for i in range(begin_idx, len(lines)) if lines[i] == _MANAGED_END),
                    len(lines) - 1)
    return lines[:begin_idx] + lines[end_idx + 1:]


def _slugify(model_id: str) -> str:
    s = model_id.strip().lower()
    if s.endswith(":free"):
        s = s[: -len(":free")]
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "model"


def _route_id(model: DiscoveredModel) -> str:
    return f"auto-{model.source}-{_slugify(model.id)}"


_RESUME_TEMPLATE = (
    'resume = ["opencode", "run", "-c", "--session", "{session}", "--auto", '
    '"--model", "%s", "--dir", "{worktree}", "{prompt}"]\n'
)


def _render_route_block(route_id: str, model: DiscoveredModel) -> str:
    """Emit a `[routes.<route_id>]` definition compatible with the existing
    hand-curated `openrouter-free-*` shape in routes.host.toml: dispatches
    via opencode, `<source>/<model-id>` addressing (matches the existing
    `openrouter/<vendor>/<model>:free` convention -- generalized to any
    source name), and the `free-endpoint` prompt_hint that makes
    adapters.build_dispatch inject the no-secrets confidentiality guard."""
    model_ref = f"{model.source}/{model.id}"
    return "".join([
        f"[routes.{route_id}]\n",
        'cli = "opencode"\n',
        f'model = "{model_ref}"\n',
        'status = "free"\n',
        'prompt_hints = ["free-endpoint"]\n',
        'probe = ["opencode", "--version"]\n',
        'dispatch_extra = ["--auto"]\n',
        _RESUME_TEMPLATE % model_ref,
        'session_discover = ["opencode", "session", "list", "--dir", "{worktree}", "--format", "json"]\n',
        'usage_source = "session-json"\n',
        "\n",
    ])


def _render_managed_block(route_ids: list[str], blocks: dict[str, str]) -> str:
    rendered_ids = ", ".join(f'"{rid}"' for rid in route_ids)
    parts = [
        _MANAGED_BEGIN,
        f"[tiers.{FREE_TIER_NAME}]\n",
        f"routes = [{rendered_ids}]\n",
        "\n",
    ]
    for rid in route_ids:
        parts.append(blocks[rid])
    parts.append(_MANAGED_END)
    return "".join(parts)


def write_routes_toml(path: Path, route_ids: list[str], blocks: dict[str, str]) -> None:
    """Rewrite `path` (routes.toml): drop any pre-existing `[tiers.free-
    high]` table (hand-authored or from a prior refresh -- TOML forbids
    redefining a table) and any pre-existing managed block, then append a
    fresh managed block. Every other tier and every other `[routes.*]`
    definition is left byte-identical (see module docstring)."""
    text = path.read_text(encoding="utf-8") if path.exists() else 'revision = "unversioned"\n'
    lines = text.splitlines(keepends=True)
    lines = _strip_table(lines, f"[tiers.{FREE_TIER_NAME}]")
    lines = _strip_managed_block(lines)
    new_text = "".join(lines)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    new_text += "\n" + _render_managed_block(route_ids, blocks)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


@dataclass
class RefreshResult:
    discovered: list[DiscoveredModel]
    route_ids: list[str]
    written: bool
    path: Path


def refresh(cfg: FreeModelsConfig | None = None, *, only: str | None = None,
            dry_run: bool = False, path: Path | None = None) -> RefreshResult:
    """Discover free models across all enabled sources and (unless dry_run)
    write them into routes.toml as `[tiers.free-high]` + `[routes.auto-*]`.
    Always returns the full plan (discovered models + computed route ids)
    regardless of dry_run, so `nyxloom free-models refresh --dry-run` can
    print exactly what a real refresh WOULD do."""
    cfg = cfg or FreeModelsConfig.load()
    models = [m for m in discover_all(cfg, only=only) if m.free]

    seen: set[tuple[str, str]] = set()
    unique: list[DiscoveredModel] = []
    for m in models:
        key = (m.source, m.id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(m)
    unique.sort(key=lambda m: (m.source, m.id))

    route_ids = [_route_id(m) for m in unique]
    blocks = {rid: _render_route_block(rid, m) for rid, m in zip(route_ids, unique)}

    target = path or paths.routes_path()
    if dry_run:
        log.debug("free-models refresh: dry-run", count=len(route_ids))
    else:
        write_routes_toml(target, route_ids, blocks)
        log.info("free-models refreshed", count=len(route_ids), path=str(target))

    return RefreshResult(discovered=unique, route_ids=route_ids,
                          written=not dry_run, path=target)
