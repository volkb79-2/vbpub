"""Dated API prices and explicitly experimental token weights.

The price table is metadata used for an API-equivalent list-price estimate. It
does not estimate a Claude subscription limit or represent a billed invoice.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import tomllib

from .model import Call, Corpus, CostState, CostStateModel, Node

PRICE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
_PRICE_FIELDS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
_FIT_CLASSES = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
_FIT_RESIDUAL_ABS_USD = 0.0001


class UsagePricingError(ValueError):
    """Raised when a weights file is invalid or incomplete."""


@dataclass(frozen=True, slots=True)
class RateSet:
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_write_5m_usd_per_mtok: float
    cache_write_1h_usd_per_mtok: float
    cache_read_usd_per_mtok: float


@dataclass(frozen=True, slots=True)
class ModelPrice:
    standard: RateSet
    fast: RateSet | None = None
    over_threshold: RateSet | None = None
    prompt_threshold_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class UsageWeights:
    schema_version: int
    provenance: dict[str, str]
    default_weight_profile: str
    weight_profiles: dict[str, dict[str, float]]
    weight_profile_descriptions: dict[str, str]
    inference_geo_multipliers: dict[str, float]
    us_inference_model_ids: frozenset[str]
    prices: dict[str, ModelPrice]
    source_sha256: str


@dataclass(frozen=True, slots=True)
class Valuation:
    weighted_units: float | None
    api_equivalent_usd: float | None
    weighted_profile: str
    api_unpriced_reasons: tuple[str, ...] = ()


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsagePricingError(f"{label} must be a number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise UsagePricingError(f"{label} must be finite and non-negative") from exc
    if not math.isfinite(result) or result < 0:
        raise UsagePricingError(f"{label} must be finite and non-negative")
    return result


def _rate_set(value: object, label: str) -> RateSet:
    if not isinstance(value, dict):
        raise UsagePricingError(f"{label} must be a table")
    absent = [field for field in _PRICE_FIELDS if field not in value]
    if absent:
        raise UsagePricingError(f"{label} is missing rates: {', '.join(absent)}")
    return RateSet(
        input_usd_per_mtok=_number(value["input"], f"{label}.input"),
        output_usd_per_mtok=_number(value["output"], f"{label}.output"),
        cache_write_5m_usd_per_mtok=_number(value["cache_write_5m"], f"{label}.cache_write_5m"),
        cache_write_1h_usd_per_mtok=_number(value["cache_write_1h"], f"{label}.cache_write_1h"),
        cache_read_usd_per_mtok=_number(value["cache_read"], f"{label}.cache_read"),
    )


def load_weights(path: str | Path | None = None) -> UsageWeights:
    """Load the packaged dated profile or a caller-selected TOML file."""
    source = Path(path) if path is not None else Path(__file__).with_name("usage-weights.toml")
    try:
        content = source.read_bytes()
        raw = tomllib.loads(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UsagePricingError(f"cannot load weights TOML: {exc}") from exc
    version = raw.get("schema_version")
    if isinstance(version, bool) or version != 1:
        raise UsagePricingError("weights schema_version must be 1")
    provenance = raw.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("pricing_url") != PRICE_URL:
        raise UsagePricingError("weights provenance must cite the official Anthropic pricing page")
    retrieved_on = provenance.get("retrieved_on")
    if not isinstance(retrieved_on, str) or not retrieved_on:
        raise UsagePricingError("weights provenance.retrieved_on is required")
    try:
        parsed_retrieved_on = date.fromisoformat(retrieved_on)
    except ValueError as exc:
        raise UsagePricingError("weights provenance.retrieved_on must be YYYY-MM-DD") from exc
    if parsed_retrieved_on.isoformat() != retrieved_on:
        raise UsagePricingError("weights provenance.retrieved_on must be YYYY-MM-DD")
    profiles_raw = raw.get("weight_profiles")
    if not isinstance(profiles_raw, dict) or not profiles_raw:
        raise UsagePricingError("weights must define at least one weight profile")
    profiles: dict[str, dict[str, float]] = {}
    profile_descriptions: dict[str, str] = {}
    for name, config in profiles_raw.items():
        if not isinstance(name, str) or not name or not isinstance(config, dict):
            raise UsagePricingError("weight profile entries must be named tables")
        weights: dict[str, float] = {}
        for field in (*_FIT_CLASSES, "cache_write_unclassified"):
            if field not in config:
                raise UsagePricingError(f"weight profile {name!r} is missing {field}")
            weights[field] = _number(config[field], f"weight_profiles.{name}.{field}")
        profiles[name] = weights
        description = config.get("description", "")
        if not isinstance(description, str):
            raise UsagePricingError(f"weight_profiles.{name}.description must be a string")
        profile_descriptions[name] = description
    default_profile = raw.get("default_weight_profile")
    if not isinstance(default_profile, str) or default_profile not in profiles:
        raise UsagePricingError("default_weight_profile must name a configured profile")
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise UsagePricingError("weights must define exact model price tables")
    prices: dict[str, ModelPrice] = {}
    for model, config in models_raw.items():
        if not isinstance(model, str) or not model or not isinstance(config, dict):
            raise UsagePricingError("model prices must use exact model IDs and tables")
        fast = _rate_set(config["fast"], f"models.{model}.fast") if "fast" in config else None
        over = _rate_set(config["over_threshold"], f"models.{model}.over_threshold") if "over_threshold" in config else None
        threshold = config.get("prompt_threshold_tokens")
        if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0):
            raise UsagePricingError(f"models.{model}.prompt_threshold_tokens must be a positive integer")
        if (over is None) != (threshold is None):
            raise UsagePricingError(f"models.{model} must configure both prompt tier rates and threshold")
        prices[model] = ModelPrice(
            standard=_rate_set(config, f"models.{model}"),
            fast=fast,
            over_threshold=over,
            prompt_threshold_tokens=threshold,
        )
    modifiers_raw = raw.get("price_modifiers")
    inference_geo = (
        modifiers_raw.get("inference_geo")
        if isinstance(modifiers_raw, dict) else None
    )
    if not isinstance(inference_geo, dict):
        raise UsagePricingError("price_modifiers.inference_geo must be a table")
    geo_multipliers: dict[str, float] = {}
    for geo in ("global", "us"):
        if geo not in inference_geo:
            raise UsagePricingError(f"price_modifiers.inference_geo.{geo} is required")
        multiplier = _number(
            inference_geo[geo], f"price_modifiers.inference_geo.{geo}",
        )
        if multiplier <= 0:
            raise UsagePricingError(f"price_modifiers.inference_geo.{geo} must be positive")
        geo_multipliers[geo] = multiplier
    us_models_raw = inference_geo.get("us_model_ids")
    if (
        not isinstance(us_models_raw, list)
        or not us_models_raw
        or any(not isinstance(model, str) or not model for model in us_models_raw)
    ):
        raise UsagePricingError("price_modifiers.inference_geo.us_model_ids must be a non-empty string list")
    us_models = frozenset(us_models_raw)
    unknown_us_models = sorted(us_models - set(prices))
    if unknown_us_models:
        raise UsagePricingError(
            "US inference modifier names models without exact prices: "
            + ", ".join(unknown_us_models)
        )
    return UsageWeights(
        schema_version=1,
        provenance={str(key): str(value) for key, value in provenance.items()},
        default_weight_profile=str(default_profile),
        weight_profiles=profiles,
        weight_profile_descriptions=profile_descriptions,
        inference_geo_multipliers=geo_multipliers,
        us_inference_model_ids=us_models,
        prices=prices,
        source_sha256=hashlib.sha256(content).hexdigest(),
    )


def _rates_dict(rates: RateSet) -> dict[str, float]:
    return {
        "input": rates.input_usd_per_mtok,
        "output": rates.output_usd_per_mtok,
        "cache_read": rates.cache_read_usd_per_mtok,
        "cache_write_5m": rates.cache_write_5m_usd_per_mtok,
        "cache_write_1h": rates.cache_write_1h_usd_per_mtok,
    }


def value_call(
    call: Call,
    weights: UsageWeights,
    *,
    profile: str | None = None,
) -> Valuation:
    """Calculate an experimental score and API-equivalent list-price estimate."""
    profile_name = profile or weights.default_weight_profile
    if profile_name not in weights.weight_profiles:
        raise UsagePricingError(f"unknown weight profile {profile_name!r}")
    w = weights.weight_profiles[profile_name]
    weighted = (
        call.input_tokens * w["input"] + call.output_tokens * w["output"]
        + call.cache_read_tokens * w["cache_read"]
        + call.cache_write_5m_tokens * w["cache_write_5m"]
        + call.cache_write_1h_tokens * w["cache_write_1h"]
        + call.cache_write_unclassified_tokens * w["cache_write_unclassified"]
    )
    reasons: list[str] = []
    price = weights.prices.get(call.model)
    if price is None:
        reasons.append("unknown_model")
    if call.cache_write_unclassified_tokens:
        reasons.append("unclassified_cache_write")
    # The dated Anthropic API documentation names standard/global as the
    # defaults. Only absent modifier facts may use those documented defaults.
    speed = "standard" if call.speed is None else call.speed
    service_tier = "standard" if call.service_tier is None else call.service_tier
    inference_geo = "global" if call.inference_geo is None else call.inference_geo
    if speed not in {"standard", "fast"}:
        reasons.append("unknown_speed")
    elif speed == "fast" and price is not None and price.fast is None:
        reasons.append("undocumented_fast_price")
    if service_tier != "standard":
        reasons.append("unknown_service_tier")
    if inference_geo not in {"global", "us"}:
        reasons.append("unknown_inference_geo")
    elif inference_geo == "us" and call.model not in weights.us_inference_model_ids:
        reasons.append("undocumented_us_inference_geo")
    if inference_geo not in weights.inference_geo_multipliers:
        reasons.append("undocumented_inference_geo")
    if price is None or reasons:
        return Valuation(weighted, None, profile_name, tuple(sorted(set(reasons))))
    rates = price.fast if speed == "fast" else price.standard
    assert rates is not None
    prompt_size = (
        call.input_tokens + call.cache_read_tokens + call.cache_write_5m_tokens
        + call.cache_write_1h_tokens + call.cache_write_unclassified_tokens
    )
    if price.prompt_threshold_tokens is not None and prompt_size > price.prompt_threshold_tokens:
        assert price.over_threshold is not None
        rates = price.over_threshold
    geo_multiplier = weights.inference_geo_multipliers[inference_geo]
    usd = geo_multiplier * (
        call.input_tokens * rates.input_usd_per_mtok
        + call.output_tokens * rates.output_usd_per_mtok
        + call.cache_read_tokens * rates.cache_read_usd_per_mtok
        + call.cache_write_5m_tokens * rates.cache_write_5m_usd_per_mtok
        + call.cache_write_1h_tokens * rates.cache_write_1h_usd_per_mtok
    ) / 1_000_000
    return Valuation(weighted, usd, profile_name)


def apply_valuation(corpus: Corpus, weights: UsageWeights, *, profile: str | None = None) -> dict[str, Any]:
    """Attach weighted and API-equivalent values to deduplicated calls."""
    calls = [call for session in corpus.sessions for node in _walk(session.root) for call in node.calls]
    unknown_models: dict[str, int] = {}
    unpriced_reasons: dict[str, int] = {}
    unpriced = 0
    call_pricing: dict[str, list[str]] = {}
    for call in calls:
        value = value_call(call, weights, profile=profile)
        call.weighted_cost = value.weighted_units
        call.usd_cost = value.api_equivalent_usd
        for reason in value.api_unpriced_reasons:
            unpriced_reasons[reason] = unpriced_reasons.get(reason, 0) + 1
        if "unknown_model" in value.api_unpriced_reasons:
            unknown_models[call.model] = unknown_models.get(call.model, 0) + 1
        if value.api_equivalent_usd is None:
            unpriced += 1
            call_pricing[call.call_id] = list(value.api_unpriced_reasons)
    return {
        "call_count": len(calls),
        "weighted_profile": profile or weights.default_weight_profile,
        "weight_profile_values": weights.weight_profiles[profile or weights.default_weight_profile],
        "weight_profile_description": weights.weight_profile_descriptions[profile or weights.default_weight_profile],
        "weighted_units": sum(call.weighted_cost or 0.0 for call in calls),
        "api_equivalent_usd": (
            sum(call.usd_cost or 0.0 for call in calls) if unpriced == 0 else None
        ),
        "priced_calls": len(calls) - unpriced,
        "unpriced_calls": unpriced,
        "unknown_models": dict(sorted(unknown_models.items())),
        "unpriced_reasons": dict(sorted(unpriced_reasons.items())),
        "call_pricing": call_pricing,
        "retrieved_on": weights.provenance["retrieved_on"],
        "pricing_url": weights.provenance["pricing_url"],
        "price_unit": weights.provenance.get("price_unit"),
        "weights_sha256": weights.source_sha256,
        "missing_modifier_policy": weights.provenance.get("missing_modifier_policy"),
    }


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _walk_report_nodes(nodes: list[dict[str, Any]]):
    for node in nodes:
        yield node
        yield from _walk_report_nodes(node["children"])


def attach_price_extension(report: dict[str, Any], summary: dict[str, Any]) -> None:
    """Fill the reserved usage-report-v1 prices extension and basis slots."""
    report_calls = report["calls"]
    unpriced_calls = [call for call in report_calls if call.get("api_equivalent_usd") is None]
    unpriced = len(unpriced_calls)
    call_count = len(report_calls)
    status = "empty" if not call_count else "complete" if not unpriced else "partial"
    total_weighted = report["totals"].get("weighted_units")
    call_unpriced_reasons = {
        call["call_id"]: summary.get("call_pricing", {}).get(call["call_id"], [])
        for call in unpriced_calls
    }
    report_reasons: dict[str, int] = {}
    unknown_models: dict[str, int] = {}
    for call in unpriced_calls:
        reasons = call_unpriced_reasons[call["call_id"]]
        for reason in reasons:
            report_reasons[reason] = report_reasons.get(reason, 0) + 1
        if "unknown_model" in reasons:
            unknown_models[call["model"]] = unknown_models.get(call["model"], 0) + 1
    shares: dict[str, float | None] = {}
    for node in _walk_report_nodes(report["nodes"]):
        weighted = node["self"].get("weighted_units")
        shares[node["node_id"]] = (
            round(weighted / total_weighted, 8)
            if isinstance(weighted, (int, float)) and total_weighted else None
        )
    report["basis"].update({
        "weighted_units": total_weighted,
        "api_equivalent_usd": report["totals"].get("api_equivalent_usd"),
        "usd": report["totals"].get("api_equivalent_usd"),
        "observed_local_share": "weighted_units / local weighted total; not account usage",
        "estimated_limit_pct": None,
    })
    report["extensions"]["prices"] = {
        "schema_version": "usage-prices-v1",
        "status": status,
        "source_url": PRICE_URL,
        "price_unit": summary.get("price_unit"),
        "price_modifiers": summary.get("price_modifiers"),
        "retrieved_on": summary.get("retrieved_on"),
        "missing_modifier_policy": summary.get("missing_modifier_policy"),
        "weights_sha256": summary.get("weights_sha256"),
        "weighted_profile": summary.get("weighted_profile"),
        "weight_profile_values": summary.get("weight_profile_values"),
        "weight_profile_description": summary.get("weight_profile_description"),
        "weighted_units": total_weighted,
        "api_equivalent_usd": report["totals"].get("api_equivalent_usd"),
        "api_equivalent_usd_is_billed_cost": False,
        "subscription_limit_usage": "unknown; API list prices and experimental weights do not measure plan usage",
        "observed_local_shares": dict(sorted(shares.items())),
        "unpriced_calls": unpriced,
        "unpriced_reasons": dict(sorted(report_reasons.items())),
        "call_unpriced_reasons": call_unpriced_reasons,
        "unknown_models": dict(sorted(unknown_models.items())),
        "estimates": True,
    }


def prices_summary(corpus: Corpus, weights: UsageWeights, valuation: dict[str, Any] | None = None) -> dict[str, Any]:
    result = valuation if valuation is not None else apply_valuation(corpus, weights)
    calls = [call for session in corpus.sessions for node in _walk(session.root) for call in node.calls]
    table: dict[str, Any] = {}
    for model, config in sorted(weights.prices.items()):
        table[model] = {
            "standard": _rates_dict(config.standard),
            "fast": _rates_dict(config.fast) if config.fast else None,
            "prompt_threshold_tokens": config.prompt_threshold_tokens,
            "over_threshold": _rates_dict(config.over_threshold) if config.over_threshold else None,
        }
    return {
        "schema_version": "usage-prices-v1",
        **result,
        "retrieved_on": weights.provenance["retrieved_on"],
        "pricing_url": weights.provenance["pricing_url"],
        "weights_sha256": weights.source_sha256,
        "missing_modifier_policy": weights.provenance.get("missing_modifier_policy"),
        "price_unit": weights.provenance.get("price_unit"),
        "price_table_usd_per_mtok": table,
        "price_modifiers": {
            "inference_geo_multipliers": weights.inference_geo_multipliers,
            "us_model_ids": sorted(weights.us_inference_model_ids),
            "fast_model_ids": sorted(
                model for model, price in weights.prices.items() if price.fast is not None
            ),
            "priority_service_tier": weights.provenance.get("priority_policy"),
        },
        "weight_profiles": weights.weight_profiles,
        "weight_profile_descriptions": weights.weight_profile_descriptions,
        "raw_token_units": sum(call.raw_token_units for call in calls),
        "measure_basis": {
            "raw_token_units": "exact sum of observed token classes",
            "weighted_units": "experimental profile score; not subscription usage",
            "api_equivalent_usd": "dated API list-price estimate; not billed cost",
            "estimates": True,
        },
        "api_equivalent_usd_is_billed_cost": False,
    }


def _cost_model_dict(state: CostState) -> dict[str, CostStateModel]:
    return {item.model: item for item in state.models}


def _call_totals(calls: list[Call], model: str) -> tuple[int, int, int, int, int]:
    selected = [call for call in calls if call.model == model]
    return (
        sum(call.input_tokens for call in selected),
        sum(call.output_tokens for call in selected),
        sum(call.cache_read_tokens for call in selected),
        sum(call.cache_write_5m_tokens for call in selected),
        sum(call.cache_write_1h_tokens for call in selected),
    )


def _state_vector(item: CostStateModel) -> tuple[int, int, int, int, int] | None:
    if item.cache_write_5m_tokens is None or item.cache_write_1h_tokens is None:
        return None
    if item.cache_write_5m_tokens + item.cache_write_1h_tokens != item.cache_creation_tokens:
        return None
    return (
        item.input_tokens, item.output_tokens, item.cache_read_tokens,
        item.cache_write_5m_tokens, item.cache_write_1h_tokens,
    )


def _solve_least_squares(rows: list[list[float]], values: list[float]) -> tuple[list[float] | None, int, list[float]]:
    """Solve a small five-column least-squares system with pivoted elimination."""
    if not rows:
        return None, 0, []
    width = len(rows[0])
    matrix = [[sum(row[i] * row[j] for row in rows) for j in range(width)] for i in range(width)]
    rhs = [sum(row[i] * value for row, value in zip(rows, values, strict=True)) for i in range(width)]
    augmented = [matrix[i] + [rhs[i]] for i in range(width)]
    rank = 0
    pivots: list[int] = []
    for column in range(width):
        pivot = max(range(rank, width), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            continue
        augmented[rank], augmented[pivot] = augmented[pivot], augmented[rank]
        scale = augmented[rank][column]
        augmented[rank] = [value / scale for value in augmented[rank]]
        for row_index in range(width):
            if row_index == rank:
                continue
            factor = augmented[row_index][column]
            augmented[row_index] = [
                left - factor * right
                for left, right in zip(augmented[row_index], augmented[rank], strict=True)
            ]
        pivots.append(column)
        rank += 1
        if rank == width:
            break
    if rank < width:
        return None, rank, []
    coefficients = [0.0] * width
    for row_index, column in enumerate(pivots):
        coefficients[column] = augmented[row_index][-1]
    residuals = [sum(x * coefficient for x, coefficient in zip(row, coefficients, strict=True)) - value for row, value in zip(rows, values, strict=True)]
    return coefficients, rank, residuals


def fit_cost_state(corpus: Corpus, weights: UsageWeights) -> dict[str, Any]:
    """Fit cost-state deltas only when scope, TTL classes, rank and residuals pass."""
    diagnostics: dict[str, int] = {
        "cost_state_records": len(corpus.cost_states),
        "malformed_cost_state_records": corpus.malformed_cost_states,
        "families_with_cost_state": 0,
        "family_scope_matches": 0,
        "monotonic_families": 0,
        "ttl_identifiable_models": 0,
        "candidate_models": 0,
        "full_rank_models": 0,
    }
    reasons: list[str] = []
    calls_by_session: dict[str, list[Call]] = {}
    for session in corpus.sessions:
        calls_by_session[session.session_id] = [call for node in _walk(session.root) for call in node.calls]
    states_by_session: dict[str, list[CostState]] = {}
    for state in corpus.cost_states:
        states_by_session.setdefault(state.session_id, []).append(state)
    models_result: dict[str, Any] = {}
    fitting_rows: dict[str, list[list[float]]] = {}
    fitting_values: dict[str, list[float]] = {}
    fitting_context_ok: dict[str, bool] = {}
    fitting_tiers: dict[str, set[str]] = {}
    for session_id, states in sorted(states_by_session.items()):
        diagnostics["families_with_cost_state"] += 1
        family_calls = calls_by_session.get(session_id, [])
        # A line sequence in a single source file is the only observed snapshot
        # order. Cross-file order cannot be inferred from startTime.
        source_ids = {state.source_id for state in states}
        if len(source_ids) != 1:
            reasons.append("family snapshot scope spans multiple source files")
            continue
        ordered = sorted(states, key=lambda item: item.source_line)
        if not family_calls or any(state.has_unknown_model_cost for state in ordered):
            reasons.append("family scope is empty or contains unknown model cost")
            continue
        if any(
            abs(sum(item.cost_usd for item in state.models) - state.total_cost_usd)
            > _FIT_RESIDUAL_ABS_USD
            for state in ordered
        ):
            reasons.append("per-model costs do not reconcile to the family total")
            continue
        final = _cost_model_dict(ordered[-1])
        call_models = {call.model for call in family_calls}
        if set(final) != call_models or any(
            _state_vector(item) is None for state in ordered for item in state.models
        ):
            reasons.append("family scope or cache TTL split is incomplete")
            continue
        if any(
            _state_vector(final[model]) != _call_totals(family_calls, model)
            for model in call_models
        ):
            reasons.append("final cost-state counters do not match the deduplicated transcript family")
            continue
        diagnostics["family_scope_matches"] += 1
        diagnostics["ttl_identifiable_models"] += len(call_models)
        monotonic = True
        previous: dict[str, CostStateModel] = {}
        for state in ordered:
            current = _cost_model_dict(state)
            if set(current) != set(final):
                monotonic = False
                break
            for model, item in current.items():
                vector = _state_vector(item)
                before = _state_vector(previous[model]) if model in previous else (0, 0, 0, 0, 0)
                if vector is None or before is None or any(a < b for a, b in zip(vector, before, strict=True)):
                    monotonic = False
                    break
                old_cost = previous[model].cost_usd if model in previous else 0.0
                if item.cost_usd < old_cost:
                    monotonic = False
                    break
            if not monotonic:
                break
            previous = current
        if not monotonic:
            reasons.append("cost-state snapshots are not monotonic in source order")
            continue
        diagnostics["monotonic_families"] += 1
        prior: dict[str, CostStateModel] = {}
        for state in ordered:
            current = _cost_model_dict(state)
            for model, item in current.items():
                vector = _state_vector(item)
                before = _state_vector(prior[model]) if model in prior else (0, 0, 0, 0, 0)
                assert vector is not None and before is not None
                delta = [now - old for now, old in zip(vector, before, strict=True)]
                previous_cost = prior[model].cost_usd if model in prior else 0.0
                fitting_rows.setdefault(model, []).append([part / 1_000_000 for part in delta])
                fitting_values.setdefault(model, []).append(item.cost_usd - previous_cost)
            prior = current
        for call in family_calls:
            if call.speed not in {None, "standard"} or call.service_tier not in {None, "standard"} or call.inference_geo not in {None, "global"}:
                for model in call_models:
                    fitting_context_ok[model] = False
            else:
                for model in call_models:
                    fitting_context_ok.setdefault(model, True)
            prompt_size = (
                call.input_tokens + call.cache_read_tokens + call.cache_write_5m_tokens
                + call.cache_write_1h_tokens + call.cache_write_unclassified_tokens
            )
            price = weights.prices.get(call.model)
            tier = (
                "over_threshold"
                if price is not None and price.prompt_threshold_tokens is not None
                and prompt_size > price.prompt_threshold_tokens
                else "standard"
            )
            fitting_tiers.setdefault(call.model, set()).add(tier)
            if len(fitting_tiers[call.model]) > 1:
                fitting_context_ok[call.model] = False
    if not corpus.cost_states:
        reasons.append("no cost-state records were found")
    for model, rows in sorted(fitting_rows.items()):
        diagnostics["candidate_models"] += 1
        coefficients, rank, residuals = _solve_least_squares(rows, fitting_values[model])
        if coefficients is None:
            continue
        diagnostics["full_rank_models"] += 1
        if any(value < -1e-10 for value in coefficients):
            reasons.append(f"{model}: fit implies a negative token-class price")
            continue
        coefficients = [max(0.0, value) for value in coefficients]
        residuals = [
            sum(value * coefficient for value, coefficient in zip(row, coefficients, strict=True)) - observed
            for row, observed in zip(rows, fitting_values[model], strict=True)
        ]
        if not fitting_context_ok.get(model, False):
            if len(fitting_tiers.get(model, ())) > 1:
                reasons.append(f"{model}: mixed prompt tiers are not identifiable from aggregate cost-state")
            else:
                reasons.append(f"{model}: documented model modifiers are not identifiable from cost-state")
            continue
        max_residual = max((abs(value) for value in residuals), default=0.0)
        max_cost = max((abs(value) for value in fitting_values[model]), default=0.0)
        bound = max(_FIT_RESIDUAL_ABS_USD, max_cost * 1e-6)
        if max_residual > bound:
            reasons.append(f"{model}: residual exceeds the {bound:.6g} USD fit bound")
            continue
        rates = weights.prices.get(model)
        coefficient_map = dict(zip(_FIT_CLASSES, coefficients, strict=True))
        disagreement: dict[str, Any] = {"has_disagreement": False, "differences_usd_per_mtok": {}}
        if rates is None:
            disagreement = {"has_disagreement": True, "reason": "no exact price profile"}
        else:
            tier_names = fitting_tiers.get(model, {"standard"})
            comparison_rates = (
                rates.over_threshold
                if tier_names == {"over_threshold"} else rates.standard
            )
            if comparison_rates is None:
                reasons.append(f"{model}: selected prompt tier has no exact documented price")
                continue
            official = _rates_dict(comparison_rates)
            differences = {
                name: round(coefficient_map[name] - official[name], 8)
                for name in _FIT_CLASSES
            }
            disagreement = {
                "has_disagreement": any(abs(value) > 0.01 for value in differences.values()),
                "differences_usd_per_mtok": differences,
            }
        models_result[model] = {
            "rank": rank,
            "coefficients_usd_per_mtok": {key: round(value, 8) for key, value in coefficient_map.items()},
            "residual_max_usd": max_residual,
            "residual_bound_usd": bound,
            "residuals_usd": residuals,
            "disagreement_vs_price_profile": disagreement,
            "cost_state_is_price_source_of_truth": False,
        }
    fit = (
        bool(models_result)
        and diagnostics["full_rank_models"] == diagnostics["candidate_models"] == len(models_result)
        and diagnostics["family_scope_matches"] == diagnostics["families_with_cost_state"]
        and diagnostics["monotonic_families"] == diagnostics["families_with_cost_state"]
    )
    if not fit:
        models_result = {}
        if diagnostics["ttl_identifiable_models"] == 0:
            reasons.append("cache TTL classes are not identifiable")
        if diagnostics["full_rank_models"] < diagnostics["candidate_models"]:
            reasons.append("token-class design matrix does not have full rank")
    return {
        "schema_version": "usage-cost-fit-v1",
        "status": "fit" if fit else "unfit",
        "models": models_result,
        "weights_sha256": weights.source_sha256,
        "price_retrieved_on": weights.provenance["retrieved_on"],
        "diagnostics": diagnostics,
        "reasons": sorted(set(reasons)),
        "source_role": "diagnostic reconciliation only; not a source of prices",
        "residual_bound_usd": _FIT_RESIDUAL_ABS_USD,
    }
