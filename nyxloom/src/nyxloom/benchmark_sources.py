"""Pluggable model benchmark and pricing sources.

This is the capability-scoring counterpart to :mod:`nyxloom.free_models`.
Sources return raw per-axis benchmark records; capability bands and routing
decisions belong to the separate capability-map module.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import paths
from .log import get_logger

log = get_logger("benchmark_sources")

_HTTP_TIMEOUT = 10.0
_MISSING = object()


@dataclass(frozen=True)
class BenchmarkRecord:
    """One source-native model benchmark and pricing record."""

    model_id: str
    source: str
    scores: dict[str, float]
    price_input: float | None
    price_output: float | None
    context_length: int | None
    raw: dict


class BenchmarkSourceError(Exception):
    """A benchmark source is misconfigured or could not be fetched."""


class BenchmarkSource(ABC):
    """Base for a pluggable benchmark source."""

    kind: str

    def __init__(self, name: str, cfg: dict[str, Any] | None = None) -> None:
        self.name = name
        self.cfg = dict(cfg or {})

    @abstractmethod
    def fetch(self) -> list[BenchmarkRecord]:
        ...


_SOURCE_KINDS: dict[str, type[BenchmarkSource]] = {}


def register_kind(kind: str):
    """Class decorator: register a BenchmarkSource subclass under ``kind``."""
    def _deco(cls: type[BenchmarkSource]) -> type[BenchmarkSource]:
        _SOURCE_KINDS[kind] = cls
        return cls
    return _deco


def _fetch_json(url: str, *, headers: dict[str, str] | None = None,
                timeout: float = _HTTP_TIMEOUT) -> Any:
    """Fetch and decode JSON using the stdlib HTTP client."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _fetch_source_json(name: str, url: str, *, headers: dict[str, str] | None = None,
                       timeout: float = _HTTP_TIMEOUT) -> Any:
    try:
        return _fetch_json(url, headers=headers, timeout=timeout)
    except Exception as exc:
        raise BenchmarkSourceError(
            f"benchmark source {name!r} fetch failed: {exc}") from exc


def _lookup(record: dict[str, Any], key: Any) -> Any:
    """Look up a direct or dotted key, tolerating absent/malformed data."""
    if not isinstance(key, str):
        return _MISSING
    if key in record:
        return record[key]
    value: Any = record
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _normal_key(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _first_value(record: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = _lookup(record, key)
        if value is not _MISSING:
            return value
    wanted = {_normal_key(key) for key in keys}
    for key, value in record.items():
        if isinstance(key, str) and _normal_key(key) in wanted:
            return value
    return _MISSING


def _as_float(value: Any) -> float | None:
    if value is _MISSING or isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is _MISSING or isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _records(payload: Any, *, path: str | None = None) -> list[Any]:
    value = payload
    if path:
        value = _lookup(payload, path) if isinstance(payload, dict) else _MISSING
    elif isinstance(payload, dict):
        value = payload.get("data", payload.get("models", []))
    if not isinstance(value, list):
        return []
    return value


def _field_key(field_map: dict[str, Any], field: str, default: str) -> Any:
    value = field_map.get(field, _MISSING)
    if value is not _MISSING:
        return value
    if field.startswith("scores."):
        scores_map = field_map.get("scores")
        axis = field.removeprefix("scores.")
        if isinstance(scores_map, dict) and axis in scores_map:
            return scores_map[axis]
        value = field_map.get(axis, _MISSING)
        if value is not _MISSING:
            return value
    return default


def _record_from_mapping(name: str, item: Any, field_map: dict[str, Any]) -> BenchmarkRecord | None:
    if not isinstance(item, dict):
        return None
    model_id = _lookup(item, _field_key(field_map, "model_id", "model_id"))
    if model_id is _MISSING:
        model_id = _first_value(item, ("model", "id", "slug", "name"))
    if model_id is _MISSING or model_id is None or str(model_id) == "":
        return None
    scores: dict[str, float] = {}
    for axis in ("intelligence", "coding", "agentic"):
        key = _field_key(field_map, f"scores.{axis}", axis)
        value = _as_float(_lookup(item, key))
        if value is not None:
            scores[axis] = value
    price_input = _as_float(_lookup(item, _field_key(field_map, "price_input", "price_input")))
    price_output = _as_float(_lookup(item, _field_key(field_map, "price_output", "price_output")))
    context_length = _as_int(_lookup(item, _field_key(field_map, "context_length", "context_length")))
    return BenchmarkRecord(
        model_id=str(model_id), source=name, scores=scores,
        price_input=price_input, price_output=price_output,
        context_length=context_length, raw=item)


def source_from_config(name: str, cfg: dict[str, Any]) -> BenchmarkSource:
    """Construct the registered source named by a config row."""
    kind = cfg.get("kind")
    try:
        source_cls = _SOURCE_KINDS.get(kind)
    except TypeError:
        source_cls = None
    if source_cls is None:
        raise BenchmarkSourceError(f"benchmark source {name!r}: unknown kind {kind!r}")
    return source_cls(name, cfg)


_AA_DEFAULT_URL = "https://artificialanalysis.ai/api/v2"


@register_kind("artificial-analysis")
class ArtificialAnalysisSource(BenchmarkSource):
    """Artificial Analysis Data API model capability and pricing listing."""

    kind = "artificial-analysis"

    def fetch(self) -> list[BenchmarkRecord]:
        key_env = self.cfg.get("key_env", "AA_API_KEY")
        key = os.environ.get(key_env) if key_env else None
        if not key:
            raise BenchmarkSourceError(
                f"benchmark source {self.name!r}: API key env {key_env!r} is unset")
        base_url = self.cfg.get("base_url", _AA_DEFAULT_URL)
        url = f"{str(base_url).rstrip('/')}/data/llms/models"
        payload = _fetch_source_json(self.name, url, headers={"x-api-key": key},
                                     timeout=_HTTP_TIMEOUT)
        aliases = {
            "model_id": ("model_id", "Model", "model", "id", "slug", "name"),
            "intelligence": ("Intelligence Index", "intelligence_index", "intelligence"),
            "coding": ("Coding Index", "coding_index", "coding"),
            "agentic": ("Agentic/Tool-use Index", "Agentic Index", "Tool-use Index",
                         "agentic_index", "tool_use_index", "agentic"),
            "price_input": ("Input price", "input_price", "price_input",
                            "input_cost_per_million", "input_cost"),
            "price_output": ("Output price", "output_price", "price_output",
                             "output_cost_per_million", "output_cost"),
            "context_length": ("Context Length", "context_length",
                                "max_context_length", "context_window"),
        }
        out: list[BenchmarkRecord] = []
        for item in _records(payload):
            if not isinstance(item, dict):
                continue
            model_id = _first_value(item, aliases["model_id"])
            if model_id is _MISSING or model_id is None or str(model_id) == "":
                continue
            scores: dict[str, float] = {}
            for axis in ("intelligence", "coding", "agentic"):
                value = _as_float(_first_value(item, aliases[axis]))
                if value is not None:
                    scores[axis] = value
            out.append(BenchmarkRecord(
                model_id=str(model_id), source=self.name, scores=scores,
                price_input=_as_float(_first_value(item, aliases["price_input"])),
                price_output=_as_float(_first_value(item, aliases["price_output"])),
                context_length=_as_int(_first_value(item, aliases["context_length"])),
                raw=item))
        return out


@register_kind("json-http")
class JSONHTTPSource(BenchmarkSource):
    """Generic JSON leaderboard source configured entirely by field mapping."""

    kind = "json-http"

    def fetch(self) -> list[BenchmarkRecord]:
        base_url = self.cfg.get("base_url")
        records_path = self.cfg.get("records_path")
        if not base_url or not records_path:
            raise BenchmarkSourceError(
                f"benchmark source {self.name!r}: base_url and records_path are required")
        key_env = self.cfg.get("key_env")
        headers: dict[str, str] = {}
        if key_env:
            key = os.environ.get(key_env)
            if not key:
                raise BenchmarkSourceError(
                    f"benchmark source {self.name!r}: API key env {key_env!r} is unset")
            header_name = self.cfg.get(
                "header_name", self.cfg.get("key_header", self.cfg.get("header", "Authorization")))
            headers[str(header_name)] = key
        payload = _fetch_source_json(self.name, str(base_url), headers=headers,
                                     timeout=_HTTP_TIMEOUT)
        field_map = self.cfg.get("field_map", {})
        if not isinstance(field_map, dict):
            field_map = {}
        out: list[BenchmarkRecord] = []
        for item in _records(payload, path=str(records_path)):
            record = _record_from_mapping(self.name, item, field_map)
            if record is not None:
                out.append(record)
        return out


_AA_DEFAULT_CONFIG = {
    "kind": "artificial-analysis",
    "base_url": _AA_DEFAULT_URL,
    "key_env": "AA_API_KEY",
    "enabled": True,
}

DEFAULT_SOURCES: dict[str, dict[str, Any]] = {
    "artificial-analysis": _AA_DEFAULT_CONFIG,
}


@dataclass
class BenchmarkConfig:
    """Optional ``[benchmarks]`` routes.toml configuration."""

    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "BenchmarkConfig":
        return cls(sources={name: dict(cfg) for name, cfg in DEFAULT_SOURCES.items()})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkConfig":
        sources = {name: dict(cfg) for name, cfg in DEFAULT_SOURCES.items()}
        for name, row in dict(data.get("sources", {})).items():
            base = sources.get(name, {})
            row = dict(row)
            kind = row.get("kind", base.get("kind"))
            if not kind:
                raise ValueError(
                    f"benchmarks.sources.{name}: 'kind' is required for a new (non-default) source")
            merged = dict(base)
            merged.update(row)
            sources[name] = merged
        return cls(sources=sources)

    @classmethod
    def load(cls, path: Path | None = None) -> "BenchmarkConfig":
        p = path or paths.routes_path()
        if not p.exists():
            log.debug("benchmarks config resolved", present=False)
            return cls.default()
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        cfg = cls.from_dict(data.get("benchmarks", {}))
        log.debug("benchmarks config resolved", present=True,
                  source_count=len(cfg.sources))
        return cfg


def fetch_all(sources: Iterable[BenchmarkSource] | BenchmarkConfig | dict[str, Any] | None = None,
              ) -> tuple[list[BenchmarkRecord], dict[str, str]]:
    """Fetch every source in isolation, returning records and source errors."""
    if sources is None:
        source_rows = BenchmarkConfig.load().sources
        source_list = [source_from_config(name, cfg)
                       for name, cfg in source_rows.items()
                       if cfg.get("enabled", True)]
    elif isinstance(sources, BenchmarkConfig):
        source_list = [source_from_config(name, cfg)
                       for name, cfg in sources.sources.items()
                       if cfg.get("enabled", True)]
    elif isinstance(sources, dict):
        source_list = [value if isinstance(value, BenchmarkSource)
                       else source_from_config(name, value)
                       for name, value in sources.items()]
    else:
        source_list = list(sources)
    records: list[BenchmarkRecord] = []
    errors: dict[str, str] = {}
    for source in source_list:
        try:
            records.extend(source.fetch())
        except Exception as exc:
            errors[source.name] = str(exc)
            log.warning("benchmark source failed", source=source.name, detail=str(exc))
    return records, errors
