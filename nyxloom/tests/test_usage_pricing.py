"""Synthetic contracts for usage prices, reset windows and anchors.

All transcript records are generated here. No fixture contains real messages
or records copied from a user's transcript store.
"""

from __future__ import annotations

import json
import stat
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib
from nyxloom.session_extract.usage import pricing
from nyxloom.session_extract.usage.anchors import (
    AnchorError,
    add_anchor,
    list_anchors,
    read_anchor_store,
)
from nyxloom.session_extract.usage.calibration import calibrate_anchors
from nyxloom.session_extract.usage.loader import load_corpus
from nyxloom.session_extract.usage.model import (
    Call,
    Node,
    Session,
    UsageEvent,
)
from nyxloom.session_extract.usage.pricing import (
    UsagePricingError,
    apply_valuation,
    attach_price_extension,
    fit_cost_state,
    load_weights,
    prices_summary,
    value_call,
)
from nyxloom.session_extract.usage.query import build_report, resolve_window
from nyxloom.session_extract.usage.render import render_csv, render_json, render_table
from nyxloom.session_extract.usage.windows import build_usage_windows

UTC = timezone.utc
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SESSION = "synthetic-session"
MESSAGE_SENTINEL = "PRIVATE_MESSAGE_SENTINEL_USAGE_804a"
TOOL_SENTINEL = "PRIVATE_TOOL_SENTINEL_USAGE_61bf"


def _call(
    *,
    call_id: str = "call-1",
    timestamp: datetime | None = None,
    model: str = "claude-sonnet-5-5",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_5m_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
    cache_write_unclassified_tokens: int = 0,
    speed: str | None = "standard",
    service_tier: str | None = "standard",
    inference_geo: str | None = "global",
) -> Call:
    moment = timestamp or NOW
    return Call(
        call_id=call_id,
        request_id=call_id,
        request_ids=[call_id],
        message_id=f"msg-{call_id}",
        timestamp=moment,
        session_id=SESSION,
        agent_id=None,
        model=model,
        effort="medium",
        speed=speed,
        service_tier=service_tier,
        inference_geo=inference_geo,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_5m_tokens=cache_write_5m_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
        cache_write_unclassified_tokens=cache_write_unclassified_tokens,
        thinking_tokens=0,
        context_size=(input_tokens + cache_read_tokens + cache_write_5m_tokens
                      + cache_write_1h_tokens + cache_write_unclassified_tokens),
        project=None,
        source_id="source-1",
        source_line=1,
        provenance=[],
        source_ids=["source-1"],
    )


def _corpus(calls: list[Call], events: list[UsageEvent] | None = None):
    root = Node(
        node_id=SESSION,
        parent_id=None,
        session_id=SESSION,
        agent_id=None,
        kind="main",
        calls=calls,
        events=events or [],
    )
    return _make_corpus(root)


def _make_corpus(root: Node):
    from nyxloom.session_extract.usage.model import Corpus

    return Corpus(sessions=[Session(root.session_id, root)])


def _assistant(session_id: str, request_id: str, usage: dict[str, int | dict]) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-10-28T11:00:00Z",
        "requestId": request_id,
        "sessionId": session_id,
        "effort": "medium",
        "message": {
            "id": f"msg-{request_id}",
            "model": "claude-sonnet-5-5",
            "usage": usage,
            "content": [
                {"type": "text", "text": MESSAGE_SENTINEL},
                {"type": "tool_use", "input": {"command": TOOL_SENTINEL}},
            ],
        },
    }


def _usage(
    *, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
    cache_5m: int = 0, cache_1h: int = 0,
) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_5m + cache_1h,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cache_5m,
            "ephemeral_1h_input_tokens": cache_1h,
        },
        "output_tokens_details": {"thinking_tokens": 0},
        "speed": "standard",
        "service_tier": "standard",
        "inference_geo": "global",
    }


def _cost_state(session_id: str, values: dict[str, int], cost: float) -> dict:
    cache_5m = values.get("cacheCreationEphemeral5mInputTokens", 0)
    cache_1h = values.get("cacheCreationEphemeral1hInputTokens", 0)
    return {
        "type": "cost-state",
        "sessionId": session_id,
        "startTime": "2026-10-28T10:00:00Z",
        "hasUnknownModelCost": False,
        "totalCostUSD": cost,
        "modelUsage": {
            "claude-sonnet-5-5": {
                "inputTokens": values.get("inputTokens", 0),
                "outputTokens": values.get("outputTokens", 0),
                "thinkingTokens": 0,
                "cacheReadInputTokens": values.get("cacheReadInputTokens", 0),
                "cacheCreationInputTokens": cache_5m + cache_1h,
                "cacheCreationEphemeral5mInputTokens": cache_5m,
                "cacheCreationEphemeral1hInputTokens": cache_1h,
                "costUSD": cost,
            },
        },
    }


def _full_rank_fit_corpus(
    *, model_id: str = "claude-sonnet-5-5", fast_first: bool = False,
):
    from nyxloom.session_extract.usage.model import (
        Corpus,
        CostState,
        CostStateModel,
        Session,
    )

    classes = (
        ("input_tokens", 2.0),
        ("output_tokens", 10.0),
        ("cache_read_tokens", 0.1),
        ("cache_write_5m_tokens", 2.5),
        ("cache_write_1h_tokens", 4.0),
    )
    calls = []
    cumulative = [0, 0, 0, 0, 0]
    states = []
    total_cost = 0.0
    session_id = "synthetic-in-memory-fit"
    for index, (field, _) in enumerate(classes):
        kwargs = {field: 1_000_000}
        if model_id == "claude-haiku-5-5":
            kwargs["input_tokens"] = kwargs.get("input_tokens", 0) + 100_001
        call = _call(
            call_id=f"fit-{index}", model=model_id,
            speed="fast" if fast_first and index == 0 else "standard",
            **kwargs,
        )
        call.session_id = session_id
        calls.append(call)
        for class_index, (class_name, class_rate) in enumerate(classes):
            cumulative[class_index] += getattr(call, class_name)
            total_cost += getattr(call, class_name) * class_rate / 1_000_000
        recorded_cost = total_cost
        model = CostStateModel(
            model=model_id,
            input_tokens=cumulative[0],
            output_tokens=cumulative[1],
            cache_read_tokens=cumulative[2],
            cache_creation_tokens=cumulative[3] + cumulative[4],
            cache_write_5m_tokens=cumulative[3],
            cache_write_1h_tokens=cumulative[4],
            cost_usd=recorded_cost,
        )
        states.append(CostState(
            session_id=session_id,
            source_id="source-fit",
            source_line=index + 1,
            has_unknown_model_cost=False,
            total_cost_usd=recorded_cost,
            models=(model,),
        ))
    root = Node(
        node_id=session_id, parent_id=None, session_id=session_id, agent_id=None,
        kind="main", calls=calls, events=[],
    )
    return Corpus(sessions=[Session(session_id, root)], cost_states=states)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_default_weights_are_versioned_and_cite_exact_anthropic_prices():
    weights = load_weights()

    assert weights.schema_version == 1
    assert weights.provenance["pricing_url"] == (
        "https://platform.claude.com/docs/en/about-claude/pricing"
    )
    assert weights.provenance["retrieved_on"] == "2026-10-28"
    assert weights.prices["claude-sonnet-5-5"].standard.input_usd_per_mtok == 2
    assert weights.prices["claude-fable-5-1"].standard.cache_read_usd_per_mtok == 0.25
    assert weights.prices["claude-opus-5-5"].fast.input_usd_per_mtok == 8
    assert weights.inference_geo_multipliers == {"global": 1.0, "us": 1.1}
    assert weights.us_inference_model_ids == frozenset(weights.prices)
    haiku = weights.prices["claude-haiku-5-5"]
    assert haiku.prompt_threshold_tokens == 100_000
    assert haiku.over_threshold.input_usd_per_mtok == 0.5
    assert weights.default_weight_profile == "e009-historical"


def test_raw_weighted_and_api_equivalent_usd_are_separate_measures():
    weights = load_weights()
    call = _call(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_5m_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
    )

    value = value_call(call, weights)

    assert call.raw_token_units == 5_000_000
    assert value.weighted_units == pytest.approx(9_350_000)
    assert value.api_equivalent_usd == pytest.approx(18.6)
    assert value.weighted_profile == "e009-historical"


def test_haiku_prompt_tier_and_documented_fast_geo_modifiers_apply_to_all_classes():
    weights = load_weights()
    haiku = _call(
        model="claude-haiku-5-5",
        input_tokens=100_001,
        output_tokens=1_000_000,
    )
    opus_fast_us = _call(
        model="claude-opus-5-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_5m_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
        speed="fast",
        inference_geo="us",
    )

    assert value_call(haiku, weights).api_equivalent_usd == pytest.approx(2.5500005)
    assert value_call(opus_fast_us, weights).api_equivalent_usd == pytest.approx(81.84)


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        (_call(model="claude-sonnet-5-5-20260101"), "unknown_model"),
        (_call(model="claude-sonnet-5-5", cache_write_unclassified_tokens=1), "unclassified_cache_write"),
        (_call(model="claude-sonnet-5-5", service_tier="priority"), "unknown_service_tier"),
        (_call(model="claude-sonnet-5-5", speed="turbo"), "unknown_speed"),
        (_call(model="claude-sonnet-5-5", inference_geo="eu"), "unknown_inference_geo"),
    ],
)
def test_unknown_price_inputs_keep_raw_totals_and_disclose_unpriced_reason(call, reason):
    value = value_call(call, load_weights())

    assert call.raw_token_units >= 0
    assert value.api_equivalent_usd is None
    assert reason in value.api_unpriced_reasons


def test_report_extension_and_all_renderers_name_estimated_measures_and_preserve_privacy(tmp_path):
    session_id = "synthetic-report"
    path = tmp_path / "project" / f"{session_id}.jsonl"
    _write_jsonl(path, [
        _assistant(session_id, "request-1", _usage(input_tokens=20, output_tokens=10)),
    ])
    corpus = load_corpus(tmp_path)
    valuation = apply_valuation(corpus, load_weights())
    report = build_report(
        corpus,
        window=resolve_window("24h", None, now=NOW),
        generated_at=NOW,
        no_labels=True,
    )
    attach_price_extension(report, valuation)

    json_text = render_json(report)
    csv_text = render_csv(report)
    table_text = render_table(report)
    assert report["schema_version"] == "usage-report-v1"
    assert report["basis"]["mode"] == "raw"
    assert report["basis"]["estimated_limit_pct"] is None
    assert report["totals"]["weighted_units"] is not None
    assert report["totals"]["api_equivalent_usd"] is not None
    assert report["extensions"]["prices"]["status"] == "complete"
    assert report["extensions"]["prices"]["estimates"] is True
    assert report["extensions"]["prices"]["api_equivalent_usd_is_billed_cost"] is False
    assert "RAW TOKENS" in table_text
    assert "WEIGHTED UNITS" in table_text
    assert "API-EQUIVALENT USD" in table_text
    assert "ESTIMATE" in table_text
    assert MESSAGE_SENTINEL not in json_text + csv_text + table_text
    assert TOOL_SENTINEL not in json_text + csv_text + table_text
    assert str(tmp_path) not in json_text + csv_text


@pytest.mark.parametrize(
    ("rate_limit_type", "expected_kind"),
    [("five_hour", "five_hour"), ("seven_day", "weekly")],
)
def test_structured_rejection_retains_observed_reset_without_percentage(
    tmp_path, rate_limit_type, expected_kind,
):
    session_id = f"synthetic-rejection-{expected_kind}"
    row = _assistant(session_id, "rejected-request", _usage())
    row.update({
        "isApiErrorMessage": True,
        "uuid": "rejection-record",
        "error": "rate_limit",
        "apiErrorStatus": 429,
        "quotaLimits": {
            "status": "rejected",
            "rateLimitType": rate_limit_type,
            "resetsAt": 1793200000,
        },
    })
    _write_jsonl(tmp_path / "project" / f"{session_id}.jsonl", [row])

    corpus = load_corpus(tmp_path)
    event = corpus.sessions[0].root.events[0]

    assert event.kind == "api_rejection"
    assert event.limit_kind == expected_kind
    assert event.reset_at is not None
    assert event.reset_at.timestamp() == 1793200000


def test_cost_state_fit_uses_complete_monotonic_family_snapshots_and_reports_residuals(tmp_path):
    project = tmp_path / "project"
    cases = [
        ({"inputTokens": 1_000_000}, 2.0),
        ({"outputTokens": 1_000_000}, 10.0),
        ({"cacheReadInputTokens": 1_000_000}, 0.1),
        ({"cacheCreationEphemeral5mInputTokens": 1_000_000}, 2.5),
        ({"cacheCreationEphemeral1hInputTokens": 1_000_000}, 4.0),
    ]
    for index, (state_values, cost) in enumerate(cases):
        session_id = f"synthetic-fit-{index}"
        if index == 0:
            half_values = {key: value // 2 for key, value in state_values.items()}
            rows = [
                _assistant(session_id, f"request-{index}", _usage(input_tokens=1_000_000)),
                _cost_state(session_id, half_values, 1.0),
                _cost_state(session_id, state_values, cost),
            ]
        else:
            usage = _usage(
                input_tokens=state_values.get("inputTokens", 0),
                output_tokens=state_values.get("outputTokens", 0),
                cache_read=state_values.get("cacheReadInputTokens", 0),
                cache_5m=state_values.get("cacheCreationEphemeral5mInputTokens", 0),
                cache_1h=state_values.get("cacheCreationEphemeral1hInputTokens", 0),
            )
            rows = [
                _assistant(session_id, f"request-{index}", usage),
                _cost_state(session_id, state_values, cost),
            ]
        _write_jsonl(project / f"{session_id}.jsonl", rows)

    result = fit_cost_state(load_corpus(tmp_path), load_weights())

    assert result["status"] == "fit"
    assert result["weights_sha256"] == load_weights().source_sha256
    assert result["price_retrieved_on"] == "2026-10-28"
    assert result["diagnostics"]["family_scope_matches"] == 5
    assert result["diagnostics"]["monotonic_families"] == 5
    assert result["models"]["claude-sonnet-5-5"]["rank"] == 5
    assert result["models"]["claude-sonnet-5-5"]["residual_max_usd"] == pytest.approx(0)
    assert result["models"]["claude-sonnet-5-5"]["coefficients_usd_per_mtok"] == pytest.approx({
        "input": 2.0,
        "output": 10.0,
        "cache_read": 0.1,
        "cache_write_5m": 2.5,
        "cache_write_1h": 4.0,
    })
    assert result["models"]["claude-sonnet-5-5"]["disagreement_vs_price_profile"]


def test_cost_state_fit_rejects_negative_class_prices_without_coefficients(tmp_path, monkeypatch):
    from nyxloom.session_extract.usage import pricing

    project = tmp_path / "project"
    cases = [
        ("inputTokens", "input_tokens", 2.0),
        ("outputTokens", "output_tokens", 10.0),
        ("cacheReadInputTokens", "cache_read", 0.1),
        ("cacheCreationEphemeral5mInputTokens", "cache_5m", 2.5),
        ("cacheCreationEphemeral1hInputTokens", "cache_1h", 4.0),
    ]
    for index, (field, usage_field, cost) in enumerate(cases):
        session_id = f"synthetic-negative-fit-{index}"
        usage = _usage(**{usage_field: 1_000_000})
        _write_jsonl(project / f"{session_id}.jsonl", [
            _assistant(session_id, f"request-{index}", usage),
            _cost_state(session_id, {field: 1_000_000}, cost),
        ])

    monkeypatch.setattr(
        pricing,
        "_solve_least_squares",
        lambda rows, values: ([2.0, 10.0, -0.1, 2.5, 4.0], 5, [0.0] * len(rows)),
    )

    result = fit_cost_state(load_corpus(tmp_path), load_weights())

    assert result["status"] == "unfit"
    assert result["models"] == {}
    assert any("negative" in reason for reason in result["reasons"])


def test_cost_state_fit_refuses_incomplete_ttl_scope_or_residuals_without_coefficients(tmp_path):
    session_id = "synthetic-unfit"
    row = _assistant(session_id, "request", _usage(input_tokens=100, cache_5m=50, cache_1h=50))
    incomplete = _cost_state(session_id, {"inputTokens": 100}, 0.0002)
    model = incomplete["modelUsage"]["claude-sonnet-5-5"]
    model.pop("cacheCreationEphemeral5mInputTokens")
    model.pop("cacheCreationEphemeral1hInputTokens")
    model["cacheCreationInputTokens"] = 100
    _write_jsonl(tmp_path / "project" / f"{session_id}.jsonl", [row, incomplete])

    result = fit_cost_state(load_corpus(tmp_path), load_weights())

    assert result["status"] == "unfit"
    assert result["models"] == {}
    assert result["diagnostics"]["ttl_identifiable_models"] == 0
    assert "cache TTL" in " ".join(result["reasons"])


def test_cost_state_fit_refuses_mixed_haiku_prompt_tiers_without_coefficients(tmp_path):
    project = tmp_path / "project"
    cases = [
        ("inputTokens", 50_000, 0.005),
        ("inputTokens", 100_001, 0.0500005),
        ("outputTokens", 1_000_000, 0.5),
        ("cacheReadInputTokens", 1_000_000, 0.05),
        ("cacheCreationEphemeral5mInputTokens", 1_000_000, 0.625),
        ("cacheCreationEphemeral1hInputTokens", 1_000_000, 1.0),
    ]
    for index, (field, count, cost) in enumerate(cases):
        session_id = f"synthetic-haiku-tier-{index}"
        usage = _usage(
            input_tokens=count if field == "inputTokens" else 0,
            output_tokens=count if field == "outputTokens" else 0,
            cache_read=count if field == "cacheReadInputTokens" else 0,
            cache_5m=count if field == "cacheCreationEphemeral5mInputTokens" else 0,
            cache_1h=count if field == "cacheCreationEphemeral1hInputTokens" else 0,
        )
        assistant = _assistant(session_id, f"request-{index}", usage)
        assistant["message"]["model"] = "claude-haiku-5-5"
        state = _cost_state(session_id, {field: count}, cost)
        state["modelUsage"]["claude-haiku-5-5"] = state["modelUsage"].pop("claude-sonnet-5-5")
        _write_jsonl(project / f"{session_id}.jsonl", [assistant, state])

    result = fit_cost_state(load_corpus(tmp_path), load_weights())

    assert result["status"] == "unfit"
    assert result["models"] == {}
    assert any("prompt tier" in reason for reason in result["reasons"])


def test_anchor_store_is_private_versioned_deterministic_and_refuses_conflicts(tmp_path):
    state = tmp_path / "private" / "usage-anchors.json"
    values = {
        "kind": "five_hour",
        "observed_at": "2026-10-28T11:00:00Z",
        "percent": 96,
        "reset_at": "2026-10-28T15:00:00Z",
        "source": "settings-usage",
        "coverage_assumption": "local-only-lower-bound",
    }

    first = add_anchor(state, **values)
    duplicate = add_anchor(state, **values)
    store = read_anchor_store(state)

    assert first["anchor_id"] == duplicate["anchor_id"]
    assert list_anchors(state) == [first]
    assert store["schema_version"] == "usage-anchors-v1"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert "path" not in json.dumps(store).lower()
    with pytest.raises(AnchorError, match="conflicting observation"):
        add_anchor(state, **{**values, "percent": 97})


def test_missing_default_anchor_store_is_empty_but_missing_explicit_store_is_an_error(
    tmp_path, monkeypatch,
):
    from nyxloom.session_extract.usage.anchors import default_anchor_path

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert not default_anchor_path().exists()
    assert list_anchors() == []

    with pytest.raises(AnchorError, match="selected anchor state file does not exist"):
        read_anchor_store(tmp_path / "misspelled-anchors.json")


@pytest.mark.parametrize(
    "overrides",
    [
        {"percent": -1},
        {"percent": 101},
        {"percent": True},
        {"kind": "rolling"},
        {"observed_at": "2026-10-28T11:00:00"},
        {"reset_at": "2026-10-28T10:00:00Z"},
        {"coverage_assumption": "guess"},
    ],
)
def test_anchor_validation_rejects_out_of_range_or_ambiguous_inputs(tmp_path, overrides):
    values = {
        "kind": "five_hour",
        "observed_at": "2026-10-28T11:00:00Z",
        "percent": 96,
        "reset_at": "2026-10-28T15:00:00Z",
        "source": "settings-usage",
        "coverage_assumption": "local-only-lower-bound",
    }

    with pytest.raises(AnchorError):
        add_anchor(tmp_path / "anchors.json", **{**values, **overrides})


def test_anchor_store_refuses_invalid_existing_schema_without_replacing_it(tmp_path):
    state = tmp_path / "anchors.json"
    state.write_text('{"schema_version":"future"}\n', encoding="utf-8")
    before = state.read_bytes()

    with pytest.raises(AnchorError, match="unsupported schema_version"):
        add_anchor(
            state,
            kind="weekly",
            observed_at="2026-10-28T11:00:00Z",
            percent=50,
            reset_at="2026-11-04T11:00:00Z",
            source="settings-usage",
            coverage_assumption="all-account-usage-observed",
        )
    assert state.read_bytes() == before


def test_windows_separate_observed_resets_weekly_reset_and_rolling_analysis(tmp_path):
    calls = [
        _call(call_id="at-reset-start", input_tokens=10,
              timestamp=datetime(2026, 10, 28, 10, 0, tzinfo=UTC)),
        _call(call_id="inside-session", input_tokens=20,
              timestamp=datetime(2026, 10, 28, 14, 0, tzinfo=UTC)),
    ]
    events = [
        UsageEvent(
            event_id="old-reset", kind="api_rejection",
            timestamp=datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
            session_id=SESSION, agent_id=None, source_record_id=None,
            source_id="source-1", source_line=1, limit_kind="five_hour",
            reset_at=datetime(2026, 10, 28, 10, 0, tzinfo=UTC),
        ),
        UsageEvent(
            event_id="new-reset", kind="api_rejection",
            timestamp=datetime(2026, 10, 28, 14, 30, tzinfo=UTC),
            session_id=SESSION, agent_id=None, source_record_id=None,
            source_id="source-1", source_line=2, limit_kind="five_hour",
            reset_at=datetime(2026, 10, 28, 15, 0, tzinfo=UTC),
        ),
    ]
    corpus = _corpus(calls, events)
    apply_valuation(corpus, load_weights())
    query = resolve_window("8d", "2026-10-28T15:01:00Z", now=NOW)
    weekly_anchor = {
        "anchor_id": "weekly-current",
        "kind": "weekly",
        "observed_at": "2026-10-28T11:00:00Z",
        "percent": 50,
        "reset_at": "2026-10-28T12:00:00Z",
        "source": "settings-usage",
        "coverage_assumption": "local-only-lower-bound",
    }
    prior_weekly_anchor = {
        **weekly_anchor,
        "anchor_id": "weekly-prior",
        "observed_at": "2026-10-21T11:00:00Z",
        "reset_at": "2026-10-21T12:00:00Z",
    }

    report = build_usage_windows(
        corpus, [prior_weekly_anchor, weekly_anchor], query, load_weights(),
    )

    five_hour = next(item for item in report["observed_intervals"] if item["kind"] == "five_hour")
    weekly = next(item for item in report["observed_intervals"] if item["kind"] == "weekly")
    assert five_hour["start"] == "2026-10-28T10:00:00Z"
    assert five_hour["end"] == "2026-10-28T15:00:00Z"
    assert five_hour["raw_token_units"] == 30
    assert weekly["start"] == "2026-10-21T12:00:00Z"
    assert weekly["end"] == "2026-10-28T12:00:00Z"
    assert report["rolling_analysis"]["kind"] == "rolling_analysis_not_account_session"
    assert report["rolling_analysis"]["start"] == "2026-10-28T10:01:00Z"
    assert report["rolling_analysis"]["calls"] == 1
    assert report["rolling_analysis"]["raw_token_units"] == 20
    assert report["estimated_limit_pct"] is None
    assert report["threshold_events"][0]["percentage"] is None
    assert report["window_semantics"]["verified_plan_scope"] == "Max plans only"
    assert "other plans" in report["window_semantics"]["documented_scope"]


def test_windows_preserve_one_observed_reset_without_inventing_interval_start():
    anchor = {
        "anchor_id": "known-reset",
        "kind": "five_hour",
        "observed_at": "2026-10-28T11:00:00Z",
        "percent": 96,
        "reset_at": "2026-10-28T15:00:00Z",
        "source": "settings-usage",
        "coverage_assumption": "local-only-lower-bound",
    }

    report = build_usage_windows(
        _corpus([]), [anchor],
        resolve_window("24h", "2026-10-28T16:00:00Z", now=NOW), load_weights(),
    )

    assert report["observed_intervals"] == []
    assert report["observed_reset_boundaries"] == [{
        "kind": "five_hour",
        "reset_at": "2026-10-28T15:00:00Z",
        "evidence": [{
            "source": "operator_ui_anchor",
            "observed_at": "2026-10-28T11:00:00Z",
            "evidence_id": "known-reset",
        }],
    }]
    assert report["reset_boundary_status"]["five_hour"]["interval_status"] == "start_unknown"
    assert report["estimated_limit_pct"] is None


def test_usage_windows_counts_nested_node_calls():
    child_call = _call(
        call_id="nested-window-call", input_tokens=10,
        timestamp=datetime(2026, 10, 28, 11, 0, tzinfo=UTC),
    )
    child = Node(
        node_id="nested-agent", parent_id=SESSION, session_id=SESSION,
        agent_id="agent-a", kind="subagent", calls=[child_call], events=[],
    )
    root = Node(
        node_id=SESSION, parent_id=None, session_id=SESSION, agent_id=None,
        kind="main", calls=[], events=[], children=[child],
    )
    report = build_usage_windows(
        _make_corpus(root), [], resolve_window("24h", None, now=NOW),
    )

    assert report["rolling_analysis"]["calls"] == 1
    assert report["rolling_analysis"]["raw_token_units"] == 10


def test_windows_clip_calls_boundaries_events_and_interval_metrics_to_half_open_query():
    calls = [
        _call(call_id="before-since", input_tokens=100,
              timestamp=datetime(2026, 10, 28, 9, 59, tzinfo=UTC)),
        _call(call_id="at-since", input_tokens=10,
              timestamp=datetime(2026, 10, 28, 10, 0, tzinfo=UTC)),
        _call(call_id="before-until", input_tokens=20,
              timestamp=datetime(2026, 10, 28, 11, 59, tzinfo=UTC)),
        _call(call_id="at-until", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 16, 0, tzinfo=UTC)),
    ]
    events = [
        UsageEvent(
            event_id="inside", kind="api_rejection",
            timestamp=datetime(2026, 10, 28, 10, 0, tzinfo=UTC),
            session_id=SESSION, agent_id=None, source_record_id=None,
            source_id="source-1", source_line=1, limit_kind="five_hour",
            reset_at=datetime(2026, 10, 28, 10, 0, tzinfo=UTC),
        ),
        UsageEvent(
            event_id="at-until", kind="api_rejection",
            timestamp=datetime(2026, 10, 28, 16, 0, tzinfo=UTC),
            session_id=SESSION, agent_id=None, source_record_id=None,
            source_id="source-1", source_line=2, limit_kind="five_hour",
            reset_at=datetime(2026, 10, 28, 16, 0, tzinfo=UTC),
        ),
    ]
    anchors = [
        {
            "anchor_id": "before", "kind": "five_hour",
            "observed_at": "2026-10-28T10:30:00Z", "reset_at": "2026-10-28T09:00:00Z",
        },
        {
            "anchor_id": "start", "kind": "five_hour",
            "observed_at": "2026-10-28T10:45:00Z", "reset_at": "2026-10-28T10:00:00Z",
        },
        {
            "anchor_id": "end", "kind": "five_hour",
            "observed_at": "2026-10-28T11:30:00Z", "reset_at": "2026-10-28T15:00:00Z",
        },
        {
            "anchor_id": "future", "kind": "five_hour",
            "observed_at": "2026-10-28T11:45:00Z", "reset_at": "2026-10-28T16:00:00Z",
        },
        {
            "anchor_id": "weekly-prior", "kind": "weekly",
            "observed_at": "2026-10-28T10:15:00Z", "reset_at": "2026-10-20T12:00:00Z",
        },
        {
            "anchor_id": "weekly-next", "kind": "weekly",
            "observed_at": "2026-10-28T11:15:00Z", "reset_at": "2026-10-27T12:00:00Z",
        },
    ]
    query = resolve_window(
        "2026-10-28T10:00:00Z", "2026-10-28T16:00:00Z", now=NOW,
    )

    report = build_usage_windows(_corpus(calls, events), anchors, query)

    assert report["query_window"] == {
        "start": "2026-10-28T10:00:00Z",
        "end": "2026-10-28T16:00:00Z",
        "semantics": "[since, until)",
    }
    assert [item["reset_at"] for item in report["observed_reset_boundaries"]
            if item["kind"] == "five_hour"] == [
        "2026-10-28T10:00:00Z", "2026-10-28T15:00:00Z",
    ]
    assert not [
        item for item in report["observed_intervals"] if item["kind"] == "weekly"
    ]
    assert [item["event_id"] for item in report["threshold_events"]] == ["inside"]
    interval = report["observed_intervals"][0]
    assert interval["start"] == "2026-10-28T10:00:00Z"
    assert interval["end"] == "2026-10-28T15:00:00Z"
    assert interval["metrics_window"] == {
        "start": "2026-10-28T10:00:00Z",
        "end": "2026-10-28T15:00:00Z",
        "semantics": "[since, until)",
    }
    assert interval["calls"] == 2
    assert interval["raw_token_units"] == 30
    assert report["rolling_analysis"]["start"] == "2026-10-28T11:00:00Z"
    assert report["rolling_analysis"]["calls"] == 1
    assert report["rolling_analysis"]["raw_token_units"] == 20

    past_query = resolve_window(
        "2026-10-28T10:00:00Z", "2026-10-28T12:00:00Z", now=NOW,
    )
    past_report = build_usage_windows(_corpus(calls, events), anchors, past_query)
    assert [item["reset_at"] for item in past_report["observed_reset_boundaries"]
            if item["kind"] == "five_hour"] == ["2026-10-28T10:00:00Z"]
    assert past_report["observed_intervals"] == []
    assert past_report["rolling_analysis"]["calls"] == 2
    assert past_report["rolling_analysis"]["raw_token_units"] == 30


def test_calibration_needs_observed_start_and_local_coverage_to_estimate_percentages():
    weights = load_weights()
    calls = [
        _call(call_id="one", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 10, 30, tzinfo=UTC)),
        _call(call_id="two", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 11, 30, tzinfo=UTC)),
    ]
    prior = {
        "anchor_id": "prior-reset",
        "kind": "five_hour",
        "observed_at": "2026-10-28T09:00:00Z",
        "percent": 20,
        "reset_at": "2026-10-28T10:00:00Z",
        "source": "settings-usage",
        "coverage_assumption": "all-account-usage-observed",
    }
    early = {
        **prior,
        "anchor_id": "early-reading",
        "observed_at": "2026-10-28T11:00:00Z",
        "percent": 40,
        "reset_at": "2026-10-28T15:00:00Z",
    }
    late = {
        **early,
        "anchor_id": "late-reading",
        "observed_at": "2026-10-28T12:00:00Z",
        "percent": 80,
        "coverage_assumption": "local-only-lower-bound",
    }
    corpus = _corpus(calls)
    apply_valuation(corpus, weights)

    result = calibrate_anchors(corpus, [prior, early, late], [])

    group = result["groups"][0]
    assert group["window_start"] == "2026-10-28T10:00:00Z"
    assert group["window_reset_at"] == "2026-10-28T15:00:00Z"
    assert group["fit_status"] == "fit-local-lower-bound"
    assert group["capacity_units"] == pytest.approx(100)
    assert group["residuals_pct_points"] == pytest.approx([0, 0])
    assert group["estimated_limit_pct"] is None
    assert result["coverage_note"].find("other devices") >= 0


def test_calibration_can_use_rejection_reset_time_as_boundary_not_as_percentage_anchor():
    weights = load_weights()
    calls = [
        _call(call_id="early", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 10, 30, tzinfo=UTC)),
        _call(call_id="late", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 11, 30, tzinfo=UTC)),
    ]
    anchors = [
        {
            "anchor_id": "early-reading",
            "kind": "five_hour",
            "observed_at": "2026-10-28T11:00:00Z",
            "percent": 40,
            "reset_at": "2026-10-28T15:00:00Z",
            "source": "settings-usage",
            "coverage_assumption": "all-account-usage-observed",
        },
        {
            "anchor_id": "late-reading",
            "kind": "five_hour",
            "observed_at": "2026-10-28T12:00:00Z",
            "percent": 80,
            "reset_at": "2026-10-28T15:00:00Z",
            "source": "settings-usage",
            "coverage_assumption": "all-account-usage-observed",
        },
    ]
    rejection = UsageEvent(
        event_id="past-reset-evidence",
        kind="api_rejection",
        timestamp=datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
        session_id=SESSION,
        agent_id=None,
        source_record_id="rejection-record",
        source_id="source-1",
        source_line=1,
        limit_kind="five_hour",
        reset_at=datetime(2026, 10, 28, 10, 0, tzinfo=UTC),
    )
    corpus = _corpus(calls)
    apply_valuation(corpus, weights)

    result = calibrate_anchors(corpus, anchors, [rejection], weights)

    assert result["groups"][0]["window_start"] == "2026-10-28T10:00:00Z"
    assert result["groups"][0]["capacity_units"] == pytest.approx(100)
    assert result["threshold_event_count"] == 1
    assert result["threshold_events_are_anchors"] is False


def test_single_rejection_is_only_a_threshold_event_and_never_a_capacity_anchor():
    rejection = UsageEvent(
        event_id="rejected",
        kind="api_rejection",
        timestamp=datetime(2026, 10, 28, 11, 0, tzinfo=UTC),
        session_id=SESSION,
        agent_id=None,
        source_record_id="rejected-record",
        source_id="source-1",
        source_line=1,
        limit_kind="five_hour",
        reset_at=datetime(2026, 10, 28, 15, 0, tzinfo=UTC),
    )

    result = calibrate_anchors(_corpus([], [rejection]), [], [])

    assert result["groups"] == []
    assert result["threshold_event_count"] == 1
    assert result["capacity_fit_status"] == "unknown"


def test_calibration_refuses_sparse_or_contradictory_ui_readings():
    weights = load_weights()
    calls = [
        _call(call_id="early-usage", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 10, 30, tzinfo=UTC)),
        _call(call_id="late-usage", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 11, 30, tzinfo=UTC)),
    ]
    reset_marker = {
        "anchor_id": "observed-reset",
        "kind": "five_hour",
        "observed_at": "2026-10-28T09:00:00Z",
        "percent": 10,
        "reset_at": "2026-10-28T10:00:00Z",
        "source": "settings-usage",
        "coverage_assumption": "local-only-lower-bound",
    }
    corpus = _corpus(calls)
    apply_valuation(corpus, weights)
    sparse = calibrate_anchors(
        corpus, [reset_marker, {
            **reset_marker,
            "anchor_id": "only-reading",
            "observed_at": "2026-10-28T11:00:00Z",
            "percent": 40,
            "reset_at": "2026-10-28T15:00:00Z",
        }], [],
    )
    assert sparse["groups"] == []
    assert any("fewer than two" in reason for reason in sparse["reasons"])

    corpus = _corpus(calls)
    apply_valuation(corpus, weights)
    contradictory = calibrate_anchors(corpus, [
        reset_marker,
        {
            **reset_marker,
            "anchor_id": "high-reading",
            "observed_at": "2026-10-28T11:00:00Z",
            "percent": 70,
            "reset_at": "2026-10-28T15:00:00Z",
        },
        {
            **reset_marker,
            "anchor_id": "lower-later-reading",
            "observed_at": "2026-10-28T12:00:00Z",
            "percent": 40,
            "reset_at": "2026-10-28T15:00:00Z",
        },
    ], [])
    assert contradictory["groups"] == []
    assert any("contradict" in reason for reason in contradictory["reasons"])


def test_usage_prices_text_includes_fit_coefficients_residuals_and_price_disagreement(
    monkeypatch, capsys,
):
    from nyxloom.session_extract.usage import loader, pricing

    from nyxloom import cli

    weights = object()
    corpus = object()
    monkeypatch.setattr(loader, "load_corpus", lambda: corpus)
    monkeypatch.setattr(pricing, "load_weights", lambda _path: weights)
    monkeypatch.setattr(pricing, "apply_valuation", lambda _corpus, _weights: {})
    monkeypatch.setattr(pricing, "prices_summary", lambda *_args: {
        "pricing_url": "https://example.invalid/pricing",
        "retrieved_on": "2026-10-28",
        "weights_sha256": "synthetic-sha256",
        "price_table_usd_per_mtok": {},
        "price_modifiers": {
            "inference_geo_multipliers": {"global": 1.0, "us": 1.1},
            "us_model_ids": [],
            "priority_service_tier": "priority is unpriced",
        },
        "weighted_profile": "synthetic-profile",
        "weighted_units": 12.0,
        "weight_profile_values": {"input": 1.0},
        "weight_profile_description": "synthetic test profile",
        "missing_modifier_policy": "unknown values stay unpriced",
        "api_equivalent_usd": 0.25,
        "raw_token_units": 100,
        "call_count": 1,
        "priced_calls": 1,
        "unpriced_calls": 0,
    })
    monkeypatch.setattr(pricing, "fit_cost_state", lambda *_args: {
        "status": "fit",
        "diagnostics": {"cost_state_records": 5},
        "reasons": [],
        "models": {
            "synthetic-model": {
                "rank": 5,
                "coefficients_usd_per_mtok": {"input": 2.0, "output": 10.0},
                "residuals_usd": [0.0, 0.00001],
                "residual_max_usd": 0.00001,
                "disagreement_vs_price_profile": {
                    "has_disagreement": True,
                    "differences_usd_per_mtok": {"input": 0.5},
                },
            },
        },
    })

    status = cli.cmd_usage_prices(SimpleNamespace(
        weights=None, fit=True, usage_prices_format="text",
    ))

    output = capsys.readouterr().out
    assert status == 0
    assert "coefficients (USD/MTok)" in output
    assert "residuals (USD)" in output
    assert "disagreement vs weights" in output


def test_private_usage_report_writer_creates_private_atomic_output_and_refuses_symlinks(tmp_path):
    from nyxloom.cli import _write_private_usage_output

    target = tmp_path / "usage.json"
    _write_private_usage_output(target, '{"schema_version":"usage-report-v1"}\n')

    assert target.read_text(encoding="utf-8") == '{"schema_version":"usage-report-v1"}\n'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    original = target.read_bytes()
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        _write_private_usage_output(link, "replacement")
    assert target.read_bytes() == original


def test_private_usage_writer_creates_missing_parent_and_rejects_non_directory_parent(
    tmp_path, monkeypatch,
):
    from pathlib import Path

    from nyxloom import cli

    nested = tmp_path / "nested" / "reports" / "usage.json"
    cli._write_private_usage_output(nested, "private")
    assert nested.read_text(encoding="utf-8") == "private"
    assert stat.S_IMODE(nested.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "nested").stat().st_mode) == 0o700
    assert stat.S_IMODE(nested.parent.stat().st_mode) == 0o700

    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    with pytest.raises(ValueError, match="parent must be a directory"):
        cli._write_private_usage_output(parent_file / "usage.json", "private")

    valid_parent = tmp_path / "false-directory-check"
    valid_parent.mkdir()
    original_mkdir = Path.mkdir
    original_is_dir = Path.is_dir
    monkeypatch.setattr(
        Path, "mkdir",
        lambda self, *args, **kwargs: (
            None if self == valid_parent else original_mkdir(self, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        Path, "is_dir",
        lambda self: False if self == valid_parent else original_is_dir(self),
    )
    with pytest.raises(ValueError, match="parent must be a directory"):
        cli._write_private_usage_output(valid_parent / "usage.json", "private")


def _weight_mapping() -> dict:
    source = Path(pricing.__file__).with_name("usage-weights.toml")
    return deepcopy(tomllib.loads(source.read_text(encoding="utf-8")))


def _load_mutated_weights(tmp_path, monkeypatch, mutate):
    raw = _weight_mapping()
    mutate(raw)
    monkeypatch.setattr(pricing.tomllib, "loads", lambda _content: raw)
    source = tmp_path / "weights.toml"
    source.write_text("schema_version = 1\n", encoding="utf-8")
    return load_weights(source)


def test_weights_refuse_fast_and_over_threshold_rates_for_the_same_model(
    tmp_path, monkeypatch,
):
    with pytest.raises(UsagePricingError, match="cannot combine fast and over_threshold"):
        _load_mutated_weights(
            tmp_path,
            monkeypatch,
            lambda raw: raw["models"]["claude-opus-5-5"].update(
                over_threshold=deepcopy(raw["models"]["claude-haiku-5-5"]["over_threshold"]),
                prompt_threshold_tokens=100_000,
            ),
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update(schema_version=2), "schema_version must be 1"),
        (lambda raw: raw.update(schema_version=True), "schema_version must be 1"),
        (lambda raw: raw.update(provenance=[]), "provenance must cite"),
        (lambda raw: raw["provenance"].update(pricing_url="https://example.invalid"), "provenance must cite"),
        (lambda raw: raw["provenance"].update(retrieved_on=4), "retrieved_on is required"),
        (lambda raw: raw["provenance"].update(retrieved_on=""), "retrieved_on is required"),
        (lambda raw: raw["provenance"].update(retrieved_on="today"), "must be YYYY-MM-DD"),
        (lambda raw: raw["provenance"].update(retrieved_on="20261028"), "must be YYYY-MM-DD"),
        (lambda raw: raw.update(weight_profiles={}), "at least one weight profile"),
        (lambda raw: raw.update(weight_profiles={"": {}}), "named tables"),
        (lambda raw: raw.update(weight_profiles={"e009-historical": "bad"}), "named tables"),
        (lambda raw: raw["weight_profiles"]["e009-historical"].pop("input"), "missing input"),
        (lambda raw: raw["weight_profiles"]["e009-historical"].update(input=True), "must be a number"),
        (lambda raw: raw["weight_profiles"]["e009-historical"].update(input=-1), "finite and non-negative"),
        (lambda raw: raw["weight_profiles"]["e009-historical"].update(description=3), "description must be a string"),
        (lambda raw: raw.update(default_weight_profile="missing"), "must name a configured profile"),
        (lambda raw: raw.update(default_weight_profile=[]), "must name a configured profile"),
        (lambda raw: raw.update(models={}), "exact model price tables"),
        (lambda raw: raw.update(models={"": {}}), "exact model IDs and tables"),
        (lambda raw: raw.update(models={"model": "bad"}), "exact model IDs and tables"),
        (lambda raw: raw["models"]["claude-opus-5-5"].update(fast="bad"), "fast must be a table"),
        (lambda raw: raw["models"]["claude-haiku-5-5"].update(over_threshold="bad"), "over_threshold must be a table"),
        (lambda raw: raw["models"]["claude-haiku-5-5"].update(prompt_threshold_tokens=True), "positive integer"),
        (lambda raw: raw["models"]["claude-haiku-5-5"].update(prompt_threshold_tokens=0), "positive integer"),
        (lambda raw: raw["models"]["claude-haiku-5-5"].pop("over_threshold"), "both prompt tier rates and threshold"),
        (lambda raw: raw["models"]["claude-sonnet-5-5"].update(prompt_threshold_tokens=100), "both prompt tier rates and threshold"),
        (lambda raw: raw["models"]["claude-sonnet-5-5"].pop("cache_read"), "missing rates"),
        (lambda raw: raw.pop("price_modifiers"), "inference_geo must be a table"),
        (lambda raw: raw.update(price_modifiers={"inference_geo": []}), "inference_geo must be a table"),
        (lambda raw: raw["price_modifiers"]["inference_geo"].pop("global"), "inference_geo.global is required"),
        (lambda raw: raw["price_modifiers"]["inference_geo"].update(us=0), "must be positive"),
        (lambda raw: raw["price_modifiers"]["inference_geo"].update(us_model_ids="model"), "non-empty string list"),
        (lambda raw: raw["price_modifiers"]["inference_geo"].update(us_model_ids=[]), "non-empty string list"),
        (lambda raw: raw["price_modifiers"]["inference_geo"].update(us_model_ids=[""]), "non-empty string list"),
        (lambda raw: raw["price_modifiers"]["inference_geo"].update(us_model_ids=["unpriced-model"]), "without exact prices"),
    ],
)
def test_weights_loader_rejects_incomplete_or_ambiguous_profiles(tmp_path, monkeypatch, mutate, message):
    with pytest.raises(UsagePricingError, match=message):
        _load_mutated_weights(tmp_path, monkeypatch, mutate)


@pytest.mark.parametrize(
    ("content", "error"),
    [(b"\xff", "cannot load weights TOML"), (b"broken = [", "cannot load weights TOML")],
)
def test_weights_loader_reports_decode_and_toml_errors(tmp_path, content, error):
    source = tmp_path / "invalid.toml"
    source.write_bytes(content)
    with pytest.raises(UsagePricingError, match=error):
        load_weights(source)


def test_weights_loader_reports_missing_file_and_overflowing_integer(tmp_path, monkeypatch):
    with pytest.raises(UsagePricingError, match="cannot load weights TOML"):
        load_weights(tmp_path / "missing.toml")

    def overflow(raw):
        raw["weight_profiles"]["e009-historical"]["input"] = 10**400

    with pytest.raises(UsagePricingError, match="finite and non-negative"):
        _load_mutated_weights(tmp_path, monkeypatch, overflow)


def test_changed_exact_price_profile_changes_usd_without_changing_raw_or_weighted_tokens(tmp_path, monkeypatch):
    call = _call(call_id="profile-change", input_tokens=1_000_000)
    original = value_call(call, load_weights())

    def change_price(raw):
        raw["models"]["claude-sonnet-5-5"]["input"] = 3.0

    changed = _load_mutated_weights(tmp_path, monkeypatch, change_price)
    revalued = value_call(call, changed)

    assert call.raw_token_units == 1_000_000
    assert revalued.weighted_units == original.weighted_units
    assert original.api_equivalent_usd == pytest.approx(2.0)
    assert revalued.api_equivalent_usd == pytest.approx(3.0)


def test_price_valuation_handles_documented_defaults_and_unavailable_modifiers():
    weights = load_weights()
    omitted = value_call(_call(speed=None, service_tier=None, inference_geo=None), weights)
    assert omitted.api_equivalent_usd == 0

    no_fast = value_call(_call(speed="fast"), weights)
    assert no_fast.api_equivalent_usd is None
    assert no_fast.api_unpriced_reasons == ("undocumented_fast_price",)

    no_geo_model = replace(weights, us_inference_model_ids=frozenset())
    no_geo = value_call(_call(inference_geo="us"), no_geo_model)
    assert "undocumented_us_inference_geo" in no_geo.api_unpriced_reasons

    no_multiplier = replace(weights, inference_geo_multipliers={"global": 1.0})
    missing_geo = value_call(_call(inference_geo="us"), no_multiplier)
    assert missing_geo.api_unpriced_reasons == ("undocumented_inference_geo",)

    unknown_with_fast = value_call(_call(model="unknown-model", speed="fast"), weights)
    assert "unknown_model" in unknown_with_fast.api_unpriced_reasons
    assert "undocumented_fast_price" not in unknown_with_fast.api_unpriced_reasons

    with pytest.raises(UsagePricingError, match="unknown weight profile"):
        value_call(_call(), weights, profile="unknown")


def test_valuation_summary_counts_nested_priced_and_unpriced_calls_and_empty_reports():
    known = _call(call_id="priced", input_tokens=100)
    unknown = _call(call_id="unknown", model="future-model", output_tokens=25)
    child = Node(
        node_id="agent-child", parent_id=SESSION, session_id=SESSION,
        agent_id="agent-id", kind="subagent", calls=[unknown], events=[],
    )
    root = Node(
        node_id=SESSION, parent_id=None, session_id=SESSION, agent_id=None,
        kind="main", calls=[known], events=[], children=[child],
    )
    corpus = _make_corpus(root)
    summary = apply_valuation(corpus, load_weights())
    assert summary["call_count"] == 2
    assert summary["priced_calls"] == 1
    assert summary["unpriced_calls"] == 1
    assert summary["unknown_models"] == {"future-model": 1}
    assert summary["api_equivalent_usd"] is None
    assert unknown.weighted_cost is not None and unknown.usd_cost is None

    report = {
        "calls": [
            {"call_id": "unknown", "model": "future-model", "api_equivalent_usd": None},
            {"call_id": "unclassified", "model": "claude-sonnet-5-5", "api_equivalent_usd": None},
        ],
        "totals": {"weighted_units": 0.0, "api_equivalent_usd": None},
        "nodes": [{
            "node_id": SESSION, "self": {"weighted_units": 0.0},
            "children": [{
                "node_id": "agent-child", "self": {"weighted_units": None},
                "children": [],
            }],
        }],
        "basis": {}, "extensions": {},
    }
    attach_price_extension(report, {
        "call_pricing": {
            "unknown": ["unknown_model"],
            "unclassified": ["unclassified_cache_write"],
        },
    })
    extension = report["extensions"]["prices"]
    assert extension["status"] == "partial"
    assert extension["unpriced_reasons"] == {
        "unknown_model": 1, "unclassified_cache_write": 1,
    }
    assert extension["unknown_models"] == {"future-model": 1}
    assert extension["observed_local_shares"] == {SESSION: None, "agent-child": None}

    empty = {
        "calls": [], "totals": {"weighted_units": 0.0, "api_equivalent_usd": 0.0},
        "nodes": [], "basis": {}, "extensions": {},
    }
    attach_price_extension(empty, {})
    assert empty["extensions"]["prices"]["status"] == "empty"
    assert empty["extensions"]["prices"]["observed_local_shares"] == {}

    prices = prices_summary(_make_corpus(root), load_weights())
    assert prices["raw_token_units"] == known.raw_token_units + unknown.raw_token_units
    assert prices["api_equivalent_usd_is_billed_cost"] is False


def test_cost_state_fit_reports_rank_and_residual_failures_without_coefficients():
    session_id = "synthetic-rank-deficient"
    from nyxloom.session_extract.usage.model import (
        Corpus,
        CostState,
        CostStateModel,
        Session,
    )

    call = _call(call_id="rank", input_tokens=1_000_000)
    call.session_id = session_id
    root = Node(
        node_id=session_id, parent_id=None, session_id=session_id, agent_id=None,
        kind="main", calls=[call], events=[],
    )

    # A single token-class dimension cannot identify five independent prices.
    cost_model = CostStateModel(
        model="claude-sonnet-5-5", input_tokens=1_000_000,
        output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
        cache_write_5m_tokens=0, cache_write_1h_tokens=0, cost_usd=99.0,
    )
    corpus = Corpus(
        sessions=[Session(session_id, root)],
        cost_states=[CostState(
            session_id=session_id, source_id="source-rank", source_line=1,
            has_unknown_model_cost=False, total_cost_usd=99.0, models=(cost_model,),
        )],
    )
    result = fit_cost_state(corpus, load_weights())
    assert result["status"] == "unfit"
    assert result["models"] == {}
    assert any("full rank" in reason for reason in result["reasons"])


def test_anchor_store_rejects_huge_percent_symlinks_bad_json_and_non_private_modes(tmp_path):
    values = {
        "kind": "five_hour", "observed_at": "2026-10-28T11:00:00Z",
        "percent": 96, "reset_at": "2026-10-28T15:00:00Z",
        "source": "settings-usage", "coverage_assumption": "local-only-lower-bound",
    }
    with pytest.raises(AnchorError, match="percent"):
        add_anchor(tmp_path / "overflow.json", **{**values, "percent": 10**400})

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(AnchorError, match="symlink"):
        add_anchor(link / "nested" / "anchors.json", **values)

    target = tmp_path / "target.json"
    add_anchor(target, **values)
    direct_link = tmp_path / "direct-link.json"
    direct_link.symlink_to(target)
    with pytest.raises(AnchorError, match="symlink"):
        read_anchor_store(direct_link)
    with pytest.raises(AnchorError, match="symlink"):
        add_anchor(direct_link, **{**values, "observed_at": "2026-10-28T12:00:00Z"})

    bad_json = tmp_path / "bad-json.json"
    bad_json.parent.chmod(0o700)
    bad_json.write_text("{", encoding="utf-8")
    bad_json.chmod(0o600)
    with pytest.raises(AnchorError, match="cannot read anchor state"):
        read_anchor_store(bad_json)

    public_file = tmp_path / "public.json"
    add_anchor(public_file, **{**values, "observed_at": "2026-10-28T12:00:00Z"})
    public_file.chmod(0o644)
    with pytest.raises(AnchorError, match="file must be private"):
        read_anchor_store(public_file)


def test_anchor_store_reports_read_error_for_directory_state_path(tmp_path):
    state_path = tmp_path / "anchor-state"
    state_path.mkdir(mode=0o700)

    with pytest.raises(AnchorError, match="cannot read anchor state"):
        read_anchor_store(state_path)


def test_anchor_store_checks_parent_permissions_and_deduplicates_ordered_readings(tmp_path):
    state = tmp_path / "private" / "anchors.json"
    first = add_anchor(
        state, kind="five_hour", observed_at="2026-10-28T11:00:00Z", percent=20,
        reset_at="2026-10-28T15:00:00Z", source="settings-usage",
        coverage_assumption="local-only-lower-bound",
    )
    second = add_anchor(
        state, kind="weekly", observed_at="2026-10-28T10:00:00Z", percent=30,
        reset_at="2026-11-04T10:00:00Z", source="account-usage-page",
        coverage_assumption="all-account-usage-observed",
    )
    assert list_anchors(state) == [second, first]
    with pytest.raises(AnchorError, match="conflicting observation"):
        add_anchor(
            state, kind="five_hour", observed_at="2026-10-28T11:00:00Z", percent=21,
            reset_at="2026-10-28T15:00:00Z", source="settings-usage",
            coverage_assumption="local-only-lower-bound",
        )

    parent = state.parent
    parent.chmod(0o755)
    with pytest.raises(AnchorError, match="directory must be private"):
        read_anchor_store(state)
    parent.chmod(0o700)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def test_windows_ignore_naive_and_malformed_anchor_timestamps_and_unrecognized_rejections():

    bad = {
        "anchor_id": "naive", "kind": "five_hour",
        "observed_at": "2026-10-28T11:00:00", "reset_at": "2026-10-28T15:00:00Z",
    }
    event = UsageEvent(
        event_id="unclassified", kind="api_rejection",
        timestamp=datetime(2026, 10, 28, 11, 0, tzinfo=UTC),
        session_id=SESSION, agent_id=None, source_record_id=None,
        source_id="source-1", source_line=1, limit_kind=None, reset_at=None,
    )
    query = resolve_window("24h", None, now=NOW)
    report = build_usage_windows(_corpus([], [event]), [bad], query)
    assert report["observed_reset_boundaries"] == []
    assert report["threshold_events"][0]["kind"] == "rate_limit"


def test_calibration_rejects_bad_events_incomplete_values_ambiguous_spans_and_large_residuals():
    weights = load_weights()
    reset_anchor = {
        "anchor_id": "reset", "kind": "five_hour",
        "observed_at": "2026-10-28T09:00:00Z", "percent": 10,
        "reset_at": "2026-10-28T10:00:00Z", "source": "settings-usage",
        "coverage_assumption": "all-account-usage-observed",
    }
    early = {
        **reset_anchor, "anchor_id": "early", "observed_at": "2026-10-28T11:00:00Z",
        "percent": 30, "reset_at": "2026-10-28T15:00:00Z",
    }
    late = {
        **early, "anchor_id": "late", "observed_at": "2026-10-28T12:00:00Z",
        "percent": 60,
    }
    calls = [
        _call(call_id="early-call", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 10, 30, tzinfo=UTC)),
        _call(call_id="late-call", input_tokens=40,
              timestamp=datetime(2026, 10, 28, 11, 30, tzinfo=UTC)),
    ]
    corpus = _corpus(calls)
    apply_valuation(corpus, weights)
    incomplete = replace(calls[1], weighted_cost=None)
    corpus = _corpus([calls[0], incomplete])
    result = calibrate_anchors(corpus, [reset_anchor, early, late], [], weights)
    assert result["groups"] == []
    assert any("weighted call values are incomplete" in reason for reason in result["reasons"])

    corpus = _corpus(calls)
    apply_valuation(corpus, weights)
    ambiguous = {
        **early, "anchor_id": "after-second-reset", "observed_at": "2026-10-28T13:00:00Z",
    }
    result = calibrate_anchors(
        corpus, [reset_anchor, early, {
            **reset_anchor, "anchor_id": "mid-reset", "observed_at": "2026-10-28T12:30:00Z",
            "percent": 50, "reset_at": "2026-10-28T12:45:00Z",
        }, ambiguous], [], weights,
    )
    assert result["groups"] == []
    assert any("ambiguous" in reason for reason in result["reasons"])

    result = calibrate_anchors(
        corpus,
        [reset_anchor, {**early, "percent": 5}, {**late, "percent": 100}],
        [{"kind": "seven_day", "reset_at": "not-a-time"}, {"kind": "other", "reset_at": None},
         {"kind": "weekly", "reset_at": datetime.fromisoformat("2026-10-28T10:00:00")},
         {"kind": "weekly", "reset_at": "2026-10-28T10:00:00"}],
        weights,
    )
    assert result["groups"] == []
    assert any("residual exceeds" in reason for reason in result["reasons"])


def test_calibration_reports_conditional_all_account_coverage_and_weekly_alias():
    weights = load_weights()
    calls = [
        _call(call_id="w1", input_tokens=40,
              timestamp=datetime(2026, 10, 22, 11, 0, tzinfo=UTC)),
        _call(call_id="w2", input_tokens=40,
              timestamp=datetime(2026, 10, 24, 11, 0, tzinfo=UTC)),
    ]
    reset = {
        "anchor_id": "week-reset", "kind": "weekly",
        "observed_at": "2026-10-21T11:00:00Z", "percent": 10,
        "reset_at": "2026-10-28T11:00:00Z", "source": "settings-usage",
        "coverage_assumption": "all-account-usage-observed",
    }
    early = {
        **reset, "anchor_id": "week-early", "observed_at": "2026-10-23T11:00:00Z",
        "percent": 40, "reset_at": "2026-11-04T11:00:00Z",
    }
    late = {
        **early, "anchor_id": "week-late", "observed_at": "2026-10-25T11:00:00Z",
        "percent": 80,
    }
    corpus = _corpus(calls)
    apply_valuation(corpus, weights)
    result = calibrate_anchors(
        corpus, [reset, early, late],
        [{"kind": "seven_day", "reset_at": "2026-10-21T11:00:00Z"}],
        weights,
    )
    group = result["groups"][0]
    assert group["kind"] == "weekly"
    assert group["fit_status"] == "fit-account-coverage"
    assert group["capacity_interpretation"] == "conditional estimate under all-account coverage"
    assert result["weight_profile"]["name"] == "e009-historical"
    assert result["weight_provenance"]["weights_sha256"] == weights.source_sha256


def test_windows_report_spacing_unverified_and_unknown_pricing_without_weights():
    first = {
        "anchor_id": "first", "kind": "five_hour", "observed_at": "2026-10-28T09:00:00Z",
        "percent": 10, "reset_at": "2026-10-28T10:00:00Z",
    }
    second = {
        **first, "anchor_id": "second", "observed_at": "2026-10-28T10:00:00Z",
        "reset_at": "2026-10-28T16:00:00Z",
    }
    unpriced = _call(
        call_id="unknown-model", model="not-priced", input_tokens=5,
        timestamp=datetime(2026, 10, 28, 17, 59, tzinfo=UTC),
    )
    event = UsageEvent(
        event_id="outside-window", kind="api_rejection",
        timestamp=datetime(2026, 10, 27, 11, 0, tzinfo=UTC),
        session_id=SESSION, agent_id=None, source_record_id=None, source_id="source-1",
        source_line=1, limit_kind="five_hour", reset_at=None,
    )
    report = build_usage_windows(
        _corpus([unpriced], [event]), [first, second],
        resolve_window("12h", "2026-10-28T18:00:00Z", now=NOW), None,
    )
    assert report["reset_boundary_status"]["five_hour"]["interval_status"] == "spacing_unverified"
    assert report["rolling_analysis"]["api_equivalent_usd"] is None
    assert report["threshold_events"] == []
    assert report["weight_profile"] is None
    assert report["price_provenance"] is None


def test_anchor_default_path_and_validation_cover_non_string_fields(tmp_path, monkeypatch):
    from nyxloom.session_extract.usage.anchors import default_anchor_path

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_anchor_path() == tmp_path / "state" / "nyxloom" / "usage-anchors-v1.json"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert default_anchor_path() == tmp_path / ".local" / "state" / "nyxloom" / "usage-anchors-v1.json"

    values = {
        "kind": "five_hour", "observed_at": "2026-10-28T11:00:00Z",
        "percent": 96, "reset_at": "2026-10-28T15:00:00Z",
        "source": "settings-usage", "coverage_assumption": "local-only-lower-bound",
    }
    for field, value in (
        ("observed_at", None), ("reset_at", "not-a-time"),
        ("kind", []), ("source", 1), ("coverage_assumption", None),
    ):
        with pytest.raises(AnchorError):
            add_anchor(tmp_path / f"invalid-{field}.json", **{**values, field: value})


def test_anchor_store_rejects_malformed_entries_duplicate_ids_and_parent_errors(tmp_path, monkeypatch):
    from nyxloom.session_extract.usage.anchors import SCHEMA_VERSION

    state = tmp_path / "private" / "anchors.json"
    state.parent.mkdir(mode=0o700)
    state.parent.chmod(0o700)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "anchors": [{
            "anchor_id": "anchor_wrong", "kind": "five_hour",
            "observed_at": "2026-10-28T11:00:00Z", "percent": 50,
            "reset_at": "2026-10-28T15:00:00Z", "source": "settings-usage",
            "coverage_assumption": "local-only-lower-bound",
        }],
    }
    state.write_text(json.dumps(payload), encoding="utf-8")
    state.chmod(0o600)
    with pytest.raises(AnchorError, match="anchor_id"):
        read_anchor_store(state)

    state.unlink()
    valid = add_anchor(
        state, kind="five_hour", observed_at="2026-10-28T11:00:00Z", percent=50,
        reset_at="2026-10-28T15:00:00Z", source="settings-usage",
        coverage_assumption="local-only-lower-bound",
    )
    payload["anchors"] = [valid, valid]
    state.write_text(json.dumps(payload), encoding="utf-8")
    state.chmod(0o600)
    with pytest.raises(AnchorError, match="duplicate identities"):
        read_anchor_store(state)

    payload["anchors"] = "not-a-list"
    state.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnchorError, match="must be a list"):
        read_anchor_store(state)

    payload["anchors"] = ["not-an-object"]
    state.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AnchorError, match="entries must be objects"):
        read_anchor_store(state)

    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("x", encoding="utf-8")
    with pytest.raises(AnchorError, match="cannot secure"):
        add_anchor(
            parent_file / "nested" / "anchors.json",
            kind="weekly", observed_at="2026-10-28T11:00:00Z", percent=50,
            reset_at="2026-11-04T11:00:00Z", source="settings-usage",
            coverage_assumption="all-account-usage-observed",
        )


def test_anchor_state_write_failure_cleans_up_temporary_file(tmp_path, monkeypatch):
    import nyxloom.session_extract.usage.anchors as anchor_module

    state = tmp_path / "private" / "anchors.json"
    monkeypatch.setattr(anchor_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(AnchorError, match="cannot write private"):
        add_anchor(
            state, kind="five_hour", observed_at="2026-10-28T11:00:00Z", percent=50,
            reset_at="2026-10-28T15:00:00Z", source="settings-usage",
            coverage_assumption="local-only-lower-bound",
        )
    assert list(state.parent.iterdir()) == []

    monkeypatch.setattr(anchor_module.os, "unlink", lambda *_args: (_ for _ in ()).throw(OSError("unlink")))
    with pytest.raises(AnchorError, match="cannot write private"):
        add_anchor(
            state, kind="five_hour", observed_at="2026-10-28T12:00:00Z", percent=50,
            reset_at="2026-10-28T15:00:00Z", source="settings-usage",
            coverage_assumption="local-only-lower-bound",
        )
    monkeypatch.undo()
    leftovers = list(state.parent.glob(".usage-anchors-*"))
    for leftover in leftovers:
        leftover.unlink()
    assert leftovers


def test_anchor_path_with_parent_component_and_duplicate_window_evidence(tmp_path):
    state = tmp_path / "nested" / ".." / "anchors.json"
    values = {
        "kind": "five_hour", "observed_at": "2026-10-28T11:00:00Z",
        "percent": 96, "reset_at": "2026-10-28T15:00:00Z",
        "source": "settings-usage", "coverage_assumption": "local-only-lower-bound",
    }
    add_anchor(state, **values)
    assert len(list_anchors(state)) == 1

    anchor = {
        "anchor_id": "same-anchor", "kind": "five_hour",
        "observed_at": "2026-10-28T11:00:00Z", "reset_at": "2026-10-28T15:00:00Z",
    }
    invalid_kind = {**anchor, "anchor_id": "unknown-kind", "kind": "monthly"}
    report = build_usage_windows(
        _corpus([]), [anchor, anchor, invalid_kind],
        resolve_window("24h", "2026-10-28T16:00:00Z", now=NOW), load_weights(),
    )
    assert len(report["observed_reset_boundaries"]) == 1
    assert len(report["observed_reset_boundaries"][0]["evidence"]) == 1


def test_calibration_handles_zero_usage_zero_percent_and_nested_calls():
    weights = load_weights()
    child_call = _call(
        call_id="nested-zero", input_tokens=0,
        timestamp=datetime(2026, 10, 28, 11, 30, tzinfo=UTC),
    )
    child = Node(
        node_id="nested-agent", parent_id=SESSION, session_id=SESSION,
        agent_id="nested-id", kind="subagent", calls=[child_call], events=[],
    )
    root = Node(
        node_id=SESSION, parent_id=None, session_id=SESSION, agent_id=None,
        kind="main", calls=[], events=[], children=[child],
    )
    corpus = _make_corpus(root)
    apply_valuation(corpus, weights)
    reset = {
        "anchor_id": "zero-reset", "kind": "five_hour",
        "observed_at": "2026-10-28T09:00:00Z", "percent": 0,
        "reset_at": "2026-10-28T10:00:00Z", "source": "settings-usage",
        "coverage_assumption": "local-only-lower-bound",
    }
    readings = [
        {**reset, "anchor_id": f"zero-{hour}", "observed_at": f"2026-10-28T{hour}:00:00Z",
         "percent": 0, "reset_at": "2026-10-28T15:00:00Z"}
        for hour in ("11", "12")
    ]

    result = calibrate_anchors(corpus, [reset, *readings], [], weights)

    assert result["groups"] == []
    assert any("cannot identify capacity" in reason for reason in result["reasons"])

    positive_call = _call(
        call_id="positive-zero-percent", input_tokens=40,
        timestamp=datetime(2026, 10, 28, 11, 30, tzinfo=UTC),
    )
    corpus = _corpus([positive_call])
    apply_valuation(corpus, weights)
    result = calibrate_anchors(
        corpus,
        [reset, {**readings[0], "percent": 0}, {**readings[1], "percent": 0}],
        [], weights,
    )
    assert result["groups"] == []
    assert any("positive capacity" in reason for reason in result["reasons"])


def test_loader_counts_invalid_cost_types_and_ignores_unrepresentable_reset_time(tmp_path):
    session_id = "synthetic-invalid-cost"
    assistant = _assistant(session_id, "bad-cost", _usage(input_tokens=10))
    bad_cost = _cost_state(session_id, {"inputTokens": 10}, 0.00002)
    bad_cost["totalCostUSD"] = "0.00002"
    rejection = _assistant(session_id, "bad-reset", _usage())
    rejection.update({
        "isApiErrorMessage": True, "uuid": "bad-reset-event", "error": "rate_limit",
        "apiErrorStatus": 429,
        "quotaLimits": {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": 10**400},
    })
    _write_jsonl(tmp_path / "project" / f"{session_id}.jsonl", [assistant, bad_cost, rejection])

    corpus = load_corpus(tmp_path)

    assert corpus.malformed_cost_states == 1
    event = corpus.sessions[0].root.events[0]
    assert event.kind == "api_rejection"
    assert event.reset_at is None


def test_loader_does_not_infer_a_window_from_unknown_limit_type_or_text_reset(tmp_path):
    session_id = "synthetic-unknown-reset"
    unknown_kind = _assistant(session_id, "unknown-kind", _usage())
    unknown_kind.update({
        "isApiErrorMessage": True, "uuid": "unknown-kind-event", "error": "rate_limit",
        "apiErrorStatus": 429,
        "quotaLimits": {"status": "rejected", "rateLimitType": "monthly", "resetsAt": 1793200000},
    })
    text_reset = _assistant(session_id, "text-reset", _usage())
    text_reset.update({
        "isApiErrorMessage": True, "uuid": "text-reset-event", "error": "rate_limit",
        "apiErrorStatus": 429,
        "quotaLimits": {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": "later"},
    })
    _write_jsonl(tmp_path / "project" / f"{session_id}.jsonl", [unknown_kind, text_reset])

    corpus = load_corpus(tmp_path)
    events = corpus.sessions[0].root.events

    assert [event.limit_kind for event in events] == ["rate_limit", "five_hour"]
    assert events[0].reset_at.timestamp() == 1793200000
    assert events[1].reset_at is None


def test_flat_report_table_renders_hierarchical_subtrees_as_rows():
    timestamp = datetime(2026, 10, 28, 11, 59, tzinfo=UTC)
    root_call = _call(call_id="flat-root", input_tokens=10, timestamp=timestamp)
    child_call = _call(call_id="flat-child", input_tokens=5, timestamp=timestamp)
    child = Node(
        node_id="flat-agent", parent_id=SESSION, session_id=SESSION,
        agent_id="agent-id", kind="subagent", calls=[child_call], events=[],
    )
    root = Node(
        node_id=SESSION, parent_id=None, session_id=SESSION, agent_id=None,
        kind="main", calls=[root_call], events=[], children=[child],
    )
    corpus = _make_corpus(root)
    valuation = apply_valuation(corpus, load_weights())
    report = build_report(
        corpus, window=resolve_window("24h", None, now=NOW), generated_at=NOW,
        no_labels=True, flat=True,
    )
    attach_price_extension(report, valuation)

    table = render_table(report)

    assert "agent agent-id" in table
    assert "RAW TOKENS" in table
    assert "API-EQUIVALENT USD (ESTIMATE)" in table


def _usage_cli_args(**overrides):
    values = {
        "since": "2026-10-28T00:00:00Z",
        "until": "2026-10-29T00:00:00Z",
        "project": None,
        "session": None,
        "depth": None,
        "min_share": 0.0,
        "flat": False,
        "sort": "cost",
        "no_labels": True,
        "weights": None,
        "usage_format": "json",
        "usage_prices_format": "json",
        "usage_windows_format": "json",
        "usage_calibrate_format": "json",
        "anchor_file": None,
        "out": None,
        "fit": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_usage_report_cli_prints_contract_json_and_privately_writes_requested_report(
    monkeypatch, capsys, tmp_path,
):
    from nyxloom.session_extract import usage
    from nyxloom.session_extract.usage import anchors

    from nyxloom import cli

    corpus = _corpus([_call(input_tokens=10)])
    monkeypatch.setattr(usage, "load_corpus", lambda **_kwargs: corpus)
    anchor_paths = []
    monkeypatch.setattr(anchors, "list_anchors", lambda path=None: anchor_paths.append(path) or [])

    assert cli.cmd_usage_report(_usage_cli_args()) == 0
    assert anchor_paths == [None]
    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert report["schema_version"] == "usage-report-v1"
    assert report["basis"]["estimated_limit_pct"] is None
    assert report["extensions"]["prices"]["api_equivalent_usd_is_billed_cost"] is False
    assert MESSAGE_SENTINEL not in stdout and TOOL_SENTINEL not in stdout

    target = tmp_path / "usage.json"
    assert cli.cmd_usage_report(_usage_cli_args(
        out=str(target), anchor_file="selected-anchors.json",
    )) == 0
    assert anchor_paths[-1] == "selected-anchors.json"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == "usage-report-v1"


def test_usage_report_continues_without_corrupt_optional_anchor_state(tmp_path, monkeypatch, capsys):
    from nyxloom import cli
    from nyxloom.session_extract import usage

    monkeypatch.setattr(usage, "load_corpus", lambda **_kwargs: _corpus([]))
    anchor_file = tmp_path / "corrupt-anchors.json"
    anchor_file.write_text("{not-json", encoding="utf-8")
    anchor_file.chmod(0o600)

    report = cli._build_usage_report_data(_usage_cli_args(anchor_file=str(anchor_file)))
    captured = capsys.readouterr()

    assert report["extensions"]["limit_anchors"]["anchor_count"] == 0
    assert report["extensions"]["limit_anchors"]["status"] == "unreadable"
    assert report["extensions"]["limit_anchors"]["warnings"] == [{
        "code": "usage_anchors_unreadable",
        "message": "Usage anchor state could not be read; no anchors were used.",
    }]
    assert report["extensions"]["chart_data"]["datasets"]["weighted_cost_buckets"][
        "cumulative_percentage_line"
    ]["status"] == "unknown"
    assert "continuing without anchors" in captured.err


@pytest.mark.parametrize("command", ["report", "charts", "windows"])
def test_usage_read_only_commands_warn_for_missing_explicit_anchor_file(
    command, tmp_path, monkeypatch, capsys,
):
    from nyxloom import cli
    from nyxloom.session_extract import usage
    from nyxloom.session_extract.usage import loader

    corpus = _corpus([])
    monkeypatch.setattr(usage, "load_corpus", lambda **_kwargs: corpus)
    monkeypatch.setattr(loader, "load_corpus", lambda **_kwargs: corpus)
    missing_anchor_file = tmp_path / "misspelled-anchors.json"
    output_path = tmp_path / "usage-charts.json"
    args = _usage_cli_args(
        anchor_file=str(missing_anchor_file),
        out=str(output_path) if command == "charts" else None,
    )

    if command == "report":
        assert cli.cmd_usage_report(args) == 0
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["extensions"]["limit_anchors"]["status"] == "unreadable"
        assert report["extensions"]["limit_anchors"]["warnings"][0]["code"] == (
            "usage_anchors_unreadable"
        )
    elif command == "charts":
        assert cli.cmd_usage_charts(args) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert json.loads(output_path.read_text(encoding="utf-8"))["schema_version"] == (
            "usage-charts-v1"
        )
    else:
        assert cli.cmd_usage_windows(args) == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["anchor_status"] == "unreadable"
        assert result["warnings"][0]["code"] == "usage_anchors_unreadable"

    assert "continuing without anchors" in captured.err


def test_usage_prices_cli_json_and_unfit_text_keep_cost_state_diagnostic_only(
    monkeypatch, capsys,
):
    from nyxloom.session_extract.usage import loader, pricing

    from nyxloom import cli

    corpus = _corpus([])
    weights = load_weights()
    monkeypatch.setattr(loader, "load_corpus", lambda: corpus)
    monkeypatch.setattr(pricing, "load_weights", lambda _path: weights)
    monkeypatch.setattr(pricing, "fit_cost_state", lambda *_args: {
        "status": "unfit", "diagnostics": {"cost_state_records": 0},
        "reasons": ["no cost-state records were found"], "models": {},
    })

    assert cli.cmd_usage_prices(_usage_cli_args(fit=True)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["fit"]["status"] == "unfit"
    assert output["api_equivalent_usd_is_billed_cost"] is False

    assert cli.cmd_usage_prices(_usage_cli_args(
        fit=True, usage_prices_format="text",
    )) == 0
    text = capsys.readouterr().out
    assert "Cost-state diagnostic fit: unfit" in text
    assert "no cost-state records were found" in text
    assert "not billed cost or subscription usage" in text

    assert cli.cmd_usage_prices(_usage_cli_args(
        fit=False, usage_prices_format="text",
    )) == 0
    no_fit_text = capsys.readouterr().out
    assert "Price profile:" in no_fit_text
    assert "Cost-state diagnostic fit:" not in no_fit_text


def test_usage_windows_cli_json_and_text_identify_separate_rolling_view(
    monkeypatch, capsys,
):
    from nyxloom.session_extract.usage import anchors, loader

    from nyxloom import cli

    corpus = _corpus([_call(input_tokens=10)])
    monkeypatch.setattr(loader, "load_corpus", lambda **_kwargs: corpus)
    monkeypatch.setattr(anchors, "list_anchors", lambda _path: [])

    args = _usage_cli_args(until="2026-10-28T13:00:00Z")
    assert cli.cmd_usage_windows(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["rolling_analysis"]["kind"] == "rolling_analysis_not_account_session"
    assert output["estimated_limit_pct"] is None

    assert cli.cmd_usage_windows(_usage_cli_args(
        until="2026-10-28T13:00:00Z", usage_windows_format="text",
    )) == 0
    text = capsys.readouterr().out
    assert "Rolling five-hour analysis (not an account session)" in text
    assert "For Max plans" in text
    assert "other plans are unverified" in text
    assert "Percentages remain unknown" in text


def test_usage_windows_cli_bounds_the_scan_and_degrades_on_unreadable_anchors(
    monkeypatch, capsys,
):
    from nyxloom.session_extract.usage import anchors, loader

    from nyxloom import cli

    corpus = _corpus([
        _call(
            call_id="past-in-range", input_tokens=10,
            timestamp=datetime(2020, 1, 1, 11, 59, tzinfo=UTC),
        ),
        _call(
            call_id="at-past-until", input_tokens=99,
            timestamp=datetime(2020, 1, 1, 12, 0, tzinfo=UTC),
        ),
    ])
    scan = {}

    def load_window(**kwargs):
        scan.update(kwargs)
        return corpus

    def unreadable(_path):
        raise AnchorError("cannot read anchor state")

    monkeypatch.setattr(loader, "load_corpus", load_window)
    monkeypatch.setattr(anchors, "list_anchors", unreadable)
    args = _usage_cli_args(
        since="2020-01-01T10:00:00Z",
        until="2020-01-01T12:00:00Z",
        anchor_file="corrupt-anchors.json",
    )

    assert cli.cmd_usage_windows(args) == 0

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert scan == {
        "since": datetime(2020, 1, 1, 10, 0, tzinfo=UTC),
        "until": datetime(2020, 1, 1, 12, 0, tzinfo=UTC),
    }
    assert output["rolling_analysis"]["calls"] == 1
    assert output["rolling_analysis"]["raw_token_units"] == 10
    assert output["query_window"]["semantics"] == "[since, until)"
    assert output["anchor_status"] == "unreadable"
    assert output["warnings"] == [{
        "code": "usage_anchors_unreadable",
        "message": "Usage anchor state could not be read; no anchors were used.",
    }]
    assert "continuing without anchors" in captured.err


def test_anchor_and_calibrate_cli_emit_only_private_metadata_and_qualified_results(
    monkeypatch, capsys, tmp_path,
):
    from nyxloom.session_extract.usage import calibration, loader, pricing

    from nyxloom import cli

    anchor_path = tmp_path / "anchors.json"
    add_args = _usage_cli_args(
        anchor_file=str(anchor_path), kind="five_hour",
        at="2026-10-28T11:00:00Z", percent=96,
        reset_at="2026-10-28T15:00:00Z", source="settings-usage",
        coverage_assumption="local-only-lower-bound",
    )
    assert cli.cmd_usage_anchor_add(add_args) == 0
    add_text = capsys.readouterr().out
    assert "Recorded observed UI percentage 96%" in add_text
    assert stat.S_IMODE(anchor_path.stat().st_mode) == 0o600

    assert cli.cmd_usage_anchor_list(_usage_cli_args(anchor_file=str(anchor_path))) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["schema_version"] == "usage-anchors-v1"
    assert listed["is_estimate"] is False
    assert "anchor_path" not in json.dumps(listed)

    corpus = _corpus([])
    weights = load_weights()
    monkeypatch.setattr(loader, "load_corpus", lambda: corpus)
    monkeypatch.setattr(pricing, "load_weights", lambda _path: weights)
    monkeypatch.setattr(calibration, "calibrate_anchors", lambda *_args: {
        "groups": [], "reasons": ["fewer than two consistent UI readings"],
        "coverage_note": "Other devices may contribute.", "weight_profile": None,
    })
    assert cli.cmd_usage_calibrate(_usage_cli_args(anchor_file=str(anchor_path))) == 0
    calibration_json = json.loads(capsys.readouterr().out)
    assert calibration_json["groups"] == []
    assert "capacity_units" not in json.dumps(calibration_json)

    assert cli.cmd_usage_calibrate(_usage_cli_args(
        anchor_file=str(anchor_path), usage_calibrate_format="text",
    )) == 0
    calibration_text = capsys.readouterr().out
    assert "Calibration: unknown" in calibration_text
    assert "Other devices may contribute" in calibration_text

    monkeypatch.setattr(calibration, "calibrate_anchors", lambda *_args: {
        "groups": [{
            "kind": "five_hour", "window_start": "2026-10-28T10:00:00Z",
            "window_reset_at": "2026-10-28T15:00:00Z",
            "fit_status": "fit-local-lower-bound", "capacity_units": 100.0,
            "capacity_interpretation": "lower bound", "anchor_fits": [],
        }],
        "reasons": [], "coverage_note": "Other devices may contribute.",
        "weight_profile": {"name": "e009-historical"},
    })
    assert cli.cmd_usage_calibrate(_usage_cli_args(
        anchor_file=str(anchor_path), usage_calibrate_format="text",
    )) == 0
    fit_text = capsys.readouterr().out
    assert "fit-local-lower-bound" in fit_text
    assert "estimated limit percentage: unknown" in fit_text


def test_usage_windows_cli_text_handles_observed_interval_and_missing_profile(
    monkeypatch, capsys,
):
    from nyxloom.session_extract.usage import anchors, loader, pricing, windows

    from nyxloom import cli

    monkeypatch.setattr(loader, "load_corpus", lambda **_kwargs: _corpus([]))
    monkeypatch.setattr(anchors, "list_anchors", lambda _path: [])
    monkeypatch.setattr(pricing, "load_weights", lambda _path: load_weights())
    monkeypatch.setattr(pricing, "apply_valuation", lambda *_args: {})
    result = {
        "rolling_analysis": {
            "start": "2026-10-28T07:00:00Z", "end": "2026-10-28T12:00:00Z",
            "raw_token_units": 0, "weighted_units": 0.0, "api_equivalent_usd": 0.0,
        },
        "weight_profile": None,
        "price_provenance": None,
        "observed_intervals": [{
            "kind": "five_hour", "start": "2026-10-28T05:00:00Z",
            "end": "2026-10-28T10:00:00Z", "raw_token_units": 12,
            "weighted_units": 8.0, "api_equivalent_usd": 0.0001,
        }],
        "threshold_events": [],
        "observed_reset_boundaries": [],
        "reset_boundary_status": {
            "five_hour": {"interval_status": "observed"},
            "weekly": {"interval_status": "start_unknown"},
        },
        "window_semantics": {
            "documented_scope": "For Max plans; other plans are unverified",
            "unverified": ["rolling session accounting"],
        },
    }
    monkeypatch.setattr(windows, "build_usage_windows", lambda *_args: result)

    assert cli.cmd_usage_windows(_usage_cli_args(
        usage_windows_format="text",
    )) == 0
    output = capsys.readouterr().out
    assert "Weight profile: unavailable" in output
    assert "Observed-reset intervals:" in output
    assert "five_hour [2026-10-28T05:00:00Z, 2026-10-28T10:00:00Z)" in output
    assert "weekly interval status: start_unknown" in output

    result["weight_profile"] = {"name": "synthetic-profile"}
    assert cli.cmd_usage_windows(_usage_cli_args(
        usage_windows_format="text",
    )) == 0
    profile_text = capsys.readouterr().out
    assert "synthetic-profile (experimental); price provenance unavailable" in profile_text


def test_private_usage_output_writer_cleans_failed_atomic_replacement(tmp_path, monkeypatch):
    from nyxloom import cli

    target = tmp_path / "report.json"

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(cli.os, "replace", fail_replace)
    with pytest.raises(ValueError, match="cannot write private usage report: replace failed"):
        cli._write_private_usage_output(target, "{}\n")
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(cli.os, "unlink", lambda *_args: (_ for _ in ()).throw(OSError("unlink failed")))
    with pytest.raises(ValueError, match="cannot write private usage report: replace failed"):
        cli._write_private_usage_output(target, "{}\n")
    monkeypatch.undo()
    leftovers = list(tmp_path.glob(".report.json.*"))
    for leftover in leftovers:
        leftover.unlink()
    assert leftovers


def test_cost_state_fit_guards_residuals_modifiers_and_unknown_exact_prices():
    weights = load_weights()

    from nyxloom.session_extract.usage.model import CostState

    residual_corpus = _full_rank_fit_corpus()
    root = residual_corpus.sessions[0].root
    root.calls.append(_call(
        call_id="conflicting-input-cost", input_tokens=1_000_000,
    ))
    root.calls[-1].session_id = root.session_id
    previous = residual_corpus.cost_states[-1]
    conflicting_total = previous.total_cost_usd + 3.0
    conflicting_model = replace(
        previous.models[0],
        input_tokens=previous.models[0].input_tokens + 1_000_000,
        cost_usd=conflicting_total,
    )
    residual_corpus.cost_states.append(CostState(
        session_id=previous.session_id,
        source_id=previous.source_id,
        source_line=6,
        has_unknown_model_cost=False,
        total_cost_usd=conflicting_total,
        models=(conflicting_model,),
    ))
    residual = fit_cost_state(residual_corpus, weights)
    assert residual["status"] == "unfit"
    assert residual["models"] == {}
    assert any("residual exceeds" in reason for reason in residual["reasons"])

    modified = fit_cost_state(_full_rank_fit_corpus(fast_first=True), weights)
    assert modified["status"] == "unfit"
    assert any("modifiers are not identifiable" in reason for reason in modified["reasons"])

    unknown = fit_cost_state(_full_rank_fit_corpus(model_id="claude-future-model"), weights)
    assert unknown["status"] == "fit"
    assert unknown["models"]["claude-future-model"]["disagreement_vs_price_profile"] == {
        "has_disagreement": True, "reason": "no exact price profile",
    }

    haiku_fit = fit_cost_state(
        _full_rank_fit_corpus(model_id="claude-haiku-5-5"), weights,
    )
    assert haiku_fit["status"] == "fit"
    assert haiku_fit["models"]["claude-haiku-5-5"]["disagreement_vs_price_profile"]["has_disagreement"]

    haiku = weights.prices["claude-haiku-5-5"]
    incomplete_prices = replace(
        weights,
        prices={
            **weights.prices,
            "claude-haiku-5-5": replace(haiku, over_threshold=None),
        },
    )
    missing_tier = fit_cost_state(
        _full_rank_fit_corpus(model_id="claude-haiku-5-5"), incomplete_prices,
    )
    assert missing_tier["status"] == "unfit"
    assert any("selected prompt tier has no exact" in reason for reason in missing_tier["reasons"])

    no_states = fit_cost_state(_corpus([_call(input_tokens=10)]), weights)
    assert no_states["status"] == "unfit"
    assert "no cost-state records were found" in no_states["reasons"]
    assert pricing._solve_least_squares([], []) == (None, 0, [])


def test_cost_state_fit_rejects_scope_cost_counter_ttl_and_monotonicity_mismatches():
    from nyxloom.session_extract.usage.model import Corpus, CostState

    weights = load_weights()
    corpus = _full_rank_fit_corpus()
    corpus.cost_states[0] = replace(corpus.cost_states[0], source_id="other-source")
    result = fit_cost_state(corpus, weights)
    assert any("multiple source files" in reason for reason in result["reasons"])

    corpus = _full_rank_fit_corpus()
    corpus.cost_states[0] = replace(corpus.cost_states[0], has_unknown_model_cost=True)
    result = fit_cost_state(corpus, weights)
    assert any("unknown model cost" in reason for reason in result["reasons"])

    empty_session = Session(
        "empty-fit", Node(
            node_id="empty-fit", parent_id=None, session_id="empty-fit", agent_id=None,
            kind="main", calls=[], events=[],
        ),
    )
    result = fit_cost_state(Corpus(
        sessions=[empty_session],
        cost_states=[CostState(
            session_id="empty-fit", source_id="empty", source_line=1,
            has_unknown_model_cost=False, total_cost_usd=0.0, models=(),
        )],
    ), weights)
    assert any("scope is empty" in reason for reason in result["reasons"])

    corpus = _full_rank_fit_corpus()
    corpus.cost_states[0] = replace(corpus.cost_states[0], total_cost_usd=99.0)
    result = fit_cost_state(corpus, weights)
    assert any("per-model costs do not reconcile" in reason for reason in result["reasons"])

    corpus = _full_rank_fit_corpus()
    final = corpus.cost_states[-1]
    final_model = replace(final.models[0], input_tokens=final.models[0].input_tokens - 1)
    corpus.cost_states[-1] = replace(final, models=(final_model,))
    result = fit_cost_state(corpus, weights)
    assert any("final cost-state counters" in reason for reason in result["reasons"])

    corpus = _full_rank_fit_corpus()
    final = corpus.cost_states[-1]
    final_model = replace(
        final.models[0], cache_creation_tokens=final.models[0].cache_creation_tokens + 1,
    )
    corpus.cost_states[-1] = replace(final, models=(final_model,))
    result = fit_cost_state(corpus, weights)
    assert any("cache TTL split is incomplete" in reason for reason in result["reasons"])

    corpus = _full_rank_fit_corpus()
    first = corpus.cost_states[0]
    extra = replace(first.models[0], model="extra-model", cost_usd=0.0)
    corpus.cost_states[0] = replace(first, models=(first.models[0], extra))
    result = fit_cost_state(corpus, weights)
    assert any("not monotonic" in reason for reason in result["reasons"])

    corpus = _full_rank_fit_corpus()
    first = corpus.cost_states[0]
    first_model = replace(first.models[0], input_tokens=2_000_000)
    corpus.cost_states[0] = replace(first, models=(first_model,))
    result = fit_cost_state(corpus, weights)
    assert any("not monotonic" in reason for reason in result["reasons"])

    corpus = _full_rank_fit_corpus()
    second = corpus.cost_states[1]
    second_model = replace(second.models[0], cost_usd=0.5)
    corpus.cost_states[1] = replace(second, total_cost_usd=0.5, models=(second_model,))
    result = fit_cost_state(corpus, weights)
    assert any("not monotonic" in reason for reason in result["reasons"])


def test_cost_state_fit_refuses_cache_ttl_sum_mismatch_and_rate_tier_uses_over_threshold():
    from nyxloom.session_extract.usage.model import (
        Corpus,
        CostState,
        CostStateModel,
        Session,
    )

    call = _call(
        call_id="ttl-mismatch", cache_write_5m_tokens=10,
        cache_write_1h_tokens=10,
    )
    session_id = call.session_id
    root = Node(
        node_id=session_id, parent_id=None, session_id=session_id, agent_id=None,
        kind="main", calls=[call], events=[],
    )
    mismatch = CostStateModel(
        model=call.model, input_tokens=0, output_tokens=0, cache_read_tokens=0,
        cache_creation_tokens=30, cache_write_5m_tokens=10,
        cache_write_1h_tokens=10, cost_usd=0.0001,
    )
    corpus = Corpus(
        sessions=[Session(session_id, root)],
        cost_states=[CostState(
            session_id=session_id, source_id="ttl", source_line=1,
            has_unknown_model_cost=False, total_cost_usd=0.0001, models=(mismatch,),
        )],
    )
    result = fit_cost_state(corpus, load_weights())
    assert any("cache TTL split is incomplete" in reason for reason in result["reasons"])


def test_unclassified_cache_write_is_unpriced_without_becoming_unknown_model():
    unknown = _call(call_id="unknown-model", model="future-model")
    call = _call(
        call_id="known-unclassified-write",
        cache_write_unclassified_tokens=5,
    )
    summary = apply_valuation(_corpus([unknown, call]), load_weights())

    assert summary["unknown_models"] == {"future-model": 1}
    assert summary["unpriced_reasons"] == {
        "unknown_model": 1, "unclassified_cache_write": 1,
    }


def test_anchor_writer_rejects_symlink_races_and_existing_public_directories(tmp_path, monkeypatch):
    import nyxloom.session_extract.usage.anchors as anchor_module

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(AnchorError, match="symlink"):
        anchor_module._write_private(link / "anchors.json", b"{}\n")

    original_check = anchor_module._has_symlink_component
    checks = iter((False, True))
    monkeypatch.setattr(
        anchor_module, "_has_symlink_component",
        lambda path: next(checks) if path == link else original_check(path),
    )
    with pytest.raises(AnchorError, match="symlink"):
        anchor_module._write_private(link / "raced.json", b"{}\n")
    monkeypatch.undo()

    public = tmp_path / "public-directory"
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    with pytest.raises(AnchorError, match="directory must be private"):
        anchor_module._write_private(public / "anchors.json", b"{}\n")
