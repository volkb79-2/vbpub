"""Tests for capability_map.py (D-R13) -- 100% diff-coverage required.

All inputs are in-memory `BenchmarkRecord`s; all file I/O uses `tmp_path`.
No live network, no live routes.toml mutation -- see conftest.py's
`tmp_state` fixture for the one test that needs an isolated NYXLOOM_STATE.
"""

from __future__ import annotations

import json
import logging
import tomllib
from unittest.mock import patch

import pytest
import structlog.contextvars

from nyxloom import capability_map, config, free_models, log
from nyxloom.band_thresholds import DEFAULT_CUTOFFS
from nyxloom.benchmark_sources import BenchmarkRecord


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """Keep structured diagnostics out of test output (matches
    test_benchmark_sources.py)."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


def _rec(model_id="vendor/model-a", source="aa", scores=None, price_input=1.0,
        price_output=2.0, context_length=128000, raw=None) -> BenchmarkRecord:
    return BenchmarkRecord(
        model_id=model_id, source=source,
        scores=dict(scores if scores is not None else
                    {"intelligence": 0.5, "coding": 0.70, "agentic": 0.9}),
        price_input=price_input, price_output=price_output,
        context_length=context_length, raw=dict(raw or {}))


# ---------------------------------------------------------------------------
# O1 -- banding is deterministic from operator cutoffs

class TestBanding:
    def test_coding_score_above_upper_cutoff_bands_top(self):
        cfg = capability_map.CapabilityMapConfig(cutoffs={"coding": [0.33, 0.66],
                                                           "intelligence": [0.33, 0.66],
                                                           "agentic": [0.33, 0.66]})
        rec = _rec(scores={"coding": 0.70})
        [cat] = capability_map.assemble_catalog([rec], cfg)
        assert cat.bands["coding"] == 3
        # the coding-axis band is exactly what implement-tier gating (out of
        # scope for B18 -- a future tier-selection consumer) reads directly
        # off the catalog per D-R13 ("implement-N tiers gate on coding").

    def test_coding_score_exactly_on_cutoff_bands_higher_inclusive(self):
        cfg = capability_map.CapabilityMapConfig(cutoffs={"coding": [0.33, 0.66],
                                                           "intelligence": [0.33, 0.66],
                                                           "agentic": [0.33, 0.66]})
        rec = _rec(scores={"coding": 0.33})
        [cat] = capability_map.assemble_catalog([rec], cfg)
        assert cat.bands["coding"] == 2

    def test_axis_missing_from_scores_bands_zero_unrated(self):
        cfg = capability_map.CapabilityMapConfig.default()
        rec = _rec(scores={"coding": 0.70})
        [cat] = capability_map.assemble_catalog([rec], cfg)
        assert cat.bands == {"coding": 3, "intelligence": 0, "agentic": 0}
        assert cat.scores == {"coding": 0.70}


# ---------------------------------------------------------------------------
# O2 -- role_gating auto vs operator

class TestRoleGating:
    def test_auto_mode_gates_on_agentic_band_thresholds(self):
        cfg = capability_map.CapabilityMapConfig(
            role_gating="auto", review_min_band=2, carve_min_band=3)
        strong = _rec(model_id="strong", scores={"agentic": 0.9})   # band 3
        weak = _rec(model_id="weak", scores={"agentic": 0.1})       # band 1
        catalog = {c.model_id: c for c in capability_map.assemble_catalog(
            [strong, weak], cfg)}
        assert catalog["strong"].may_review is True
        assert catalog["strong"].may_carve is True
        assert catalog["weak"].may_review is False
        assert catalog["weak"].may_carve is False

    def test_operator_mode_grants_override_band_in_both_directions(self):
        cfg = capability_map.CapabilityMapConfig(
            role_gating="operator",
            review_grants=["granted-weak"], carve_grants=["granted-weak"])
        strong_ungranted = _rec(model_id="strong-ungranted", scores={"agentic": 0.9})
        granted_weak = _rec(model_id="granted-weak", scores={"agentic": 0.1})
        catalog = {c.model_id: c for c in capability_map.assemble_catalog(
            [strong_ungranted, granted_weak], cfg)}
        # high band alone does NOT grant a role in operator mode
        assert catalog["strong-ungranted"].may_review is False
        assert catalog["strong-ungranted"].may_carve is False
        # an operator grant overrides a low band
        assert catalog["granted-weak"].may_review is True
        assert catalog["granted-weak"].may_carve is True
        # bands are still computed regardless of gating mode
        assert catalog["strong-ungranted"].bands["agentic"] == 3
        assert catalog["granted-weak"].bands["agentic"] == 1


# ---------------------------------------------------------------------------
# O3 -- hard filters exclude regardless of score

class TestHardFilters:
    def test_below_min_context_is_excluded(self):
        cfg = capability_map.CapabilityMapConfig(min_context=100_000)
        low_ctx = _rec(model_id="low-ctx", context_length=8_000)
        high_ctx = _rec(model_id="high-ctx", context_length=200_000)
        no_ctx = _rec(model_id="no-ctx", context_length=None)
        catalog = capability_map.assemble_catalog([low_ctx, high_ctx, no_ctx], cfg)
        ids = {c.model_id for c in catalog}
        assert ids == {"high-ctx"}

    def test_missing_required_flag_is_excluded(self):
        cfg = capability_map.CapabilityMapConfig(required_flags=["tool_use"])
        has_flag = _rec(model_id="has-flag", raw={"tool_use": True})
        missing_flag = _rec(model_id="missing-flag", raw={})
        false_flag = _rec(model_id="false-flag", raw={"tool_use": False})
        catalog = capability_map.assemble_catalog(
            [has_flag, missing_flag, false_flag], cfg)
        ids = {c.model_id for c in catalog}
        assert ids == {"has-flag"}

    def test_excluded_records_never_appear_review_or_carve_eligible(self):
        cfg = capability_map.CapabilityMapConfig(
            role_gating="auto", min_context=100_000, review_min_band=1, carve_min_band=1)
        excluded = _rec(model_id="excluded", context_length=1_000,
                        scores={"agentic": 0.99})  # would qualify on band alone
        catalog = capability_map.assemble_catalog([excluded], cfg)
        assert catalog == []
        assert all(c.model_id != "excluded" for c in catalog if c.may_review or c.may_carve)


# ---------------------------------------------------------------------------
# O4 -- writer idempotence

class TestWriterIdempotence:
    def test_write_adds_trailing_newline_when_source_lacks_one(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text('revision = "r1"\n\n[tiers.flash-high]\nroutes = ["a"]',
                    encoding="utf-8")  # no trailing newline
        cfg = capability_map.CapabilityMapConfig.default()
        catalog = capability_map.assemble_catalog([_rec()], cfg)
        capability_map.write_capability_catalog(p, catalog)
        text = p.read_text(encoding="utf-8")
        assert '[tiers.flash-high]\nroutes = ["a"]\n' in text
        assert capability_map._MANAGED_BEGIN in text

    def test_writing_twice_is_byte_identical(self, tmp_path):
        p = tmp_path / "routes.toml"
        cfg = capability_map.CapabilityMapConfig.default()
        catalog = capability_map.assemble_catalog([_rec()], cfg)
        capability_map.write_capability_catalog(p, catalog)
        first = p.read_text(encoding="utf-8")
        capability_map.write_capability_catalog(p, catalog)
        second = p.read_text(encoding="utf-8")
        assert first == second
        assert first.count(capability_map._MANAGED_BEGIN) == 1
        assert first.count(capability_map._MANAGED_END) == 1


# ---------------------------------------------------------------------------
# O5 -- writer non-clobber (free_models block + hand-authored tier survive)

class TestWriterNonClobber:
    def test_preserves_free_models_block_and_hand_authored_tier(self, tmp_path):
        p = tmp_path / "routes.toml"
        seed = "".join([
            'revision = "r1"\n\n',
            "[tiers.implement-1]\n",
            'routes = ["some-route"]\n\n',
            "[routes.some-route]\n",
            'cli = "opencode"\n\n',
            free_models._MANAGED_BEGIN,
            "[tiers.free-high]\n",
            'routes = ["auto-groq-model-a"]\n\n',
            "[routes.auto-groq-model-a]\n",
            'cli = "opencode"\n',
            'model = "groq/model-a"\n',
            free_models._MANAGED_END,
        ])
        p.write_text(seed, encoding="utf-8")

        cfg = capability_map.CapabilityMapConfig.default()
        catalog = capability_map.assemble_catalog([_rec(model_id="new-model")], cfg)
        capability_map.write_capability_catalog(p, catalog)

        text = p.read_text(encoding="utf-8")
        # hand-authored tier + route untouched
        assert '[tiers.implement-1]\nroutes = ["some-route"]' in text
        assert "[routes.some-route]" in text
        # free_models' own managed block untouched, still present exactly once
        assert text.count(free_models._MANAGED_BEGIN) == 1
        assert text.count(free_models._MANAGED_END) == 1
        assert '[tiers.free-high]\nroutes = ["auto-groq-model-a"]' in text
        assert "[routes.auto-groq-model-a]" in text
        # our own managed block appended, distinct markers, exactly once
        assert text.count(capability_map._MANAGED_BEGIN) == 1
        assert text.count(capability_map._MANAGED_END) == 1
        assert capability_map._MANAGED_BEGIN != free_models._MANAGED_BEGIN
        assert '"new-model"' in text


class TestWriterOptionalFields:
    def test_none_price_and_context_fields_are_omitted_not_null(self, tmp_path):
        p = tmp_path / "routes.toml"
        cfg = capability_map.CapabilityMapConfig.default()
        rec = _rec(model_id="no-price-model", price_input=None, price_output=None,
                   context_length=None)
        catalog = capability_map.assemble_catalog([rec], cfg)
        capability_map.write_capability_catalog(p, catalog)
        text = p.read_text(encoding="utf-8")
        assert "price_input" not in text
        assert "price_output" not in text
        assert "context_length" not in text
        data = tomllib.loads(text)  # still valid TOML with the fields absent
        [row] = data["capability_catalog"]["records"]
        assert "price_input" not in row
        assert "price_output" not in row
        assert "context_length" not in row


# ---------------------------------------------------------------------------
# O6 -- round-trip parse (tomllib + config.Routes.load)

class TestRoundTrip:
    def test_written_catalog_round_trips_through_tomllib_and_routes_load(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text('revision = "r1"\n', encoding="utf-8")
        cfg = capability_map.CapabilityMapConfig(role_gating="auto",
                                                 review_min_band=1, carve_min_band=1)
        rec = _rec(model_id="round-trip-model", scores={"agentic": 0.9, "coding": 0.5},
                   raw={"vision": True})
        [cat] = capability_map.assemble_catalog([rec], cfg)
        capability_map.write_capability_catalog(p, [cat])

        text = p.read_text(encoding="utf-8")
        data = tomllib.loads(text)  # raises on invalid TOML
        [row] = data["capability_catalog"]["records"]
        assert row["model_id"] == cat.model_id
        assert row["bands"] == cat.bands
        assert row["may_review"] == cat.may_review
        assert row["may_carve"] == cat.may_carve
        assert json.loads(row["raw_json"]) == cat.raw

        # config.Routes.load (frozen core) parses the same file without
        # raising -- it only reads data["tiers"]/data["routes"], so the
        # capability_catalog table is simply ignored, no collision.
        routes = config.Routes.load(p)
        assert routes.tiers == {}
        assert routes.routes == {}


# ---------------------------------------------------------------------------
# O7 -- empty catalog

class TestEmptyCatalog:
    def test_empty_catalog_writes_valid_idempotent_block(self, tmp_path):
        p = tmp_path / "routes.toml"
        capability_map.write_capability_catalog(p, [])
        first = p.read_text(encoding="utf-8")
        data = tomllib.loads(first)
        assert data["capability_catalog"]["records"] == []

        capability_map.write_capability_catalog(p, [])
        second = p.read_text(encoding="utf-8")
        assert first == second
        assert second.count(capability_map._MANAGED_BEGIN) == 1


# ---------------------------------------------------------------------------
# CapabilityMapConfig -- default()/from_dict()/load()

class TestCapabilityMapConfig:
    def test_default_matches_expected_baseline_and_is_independent_copy(self):
        cfg1 = capability_map.CapabilityMapConfig.default()
        cfg2 = capability_map.CapabilityMapConfig.default()
        assert cfg1.role_gating == "operator"
        assert cfg1.cutoffs == DEFAULT_CUTOFFS
        assert cfg1.review_min_band == 2
        assert cfg1.carve_min_band == 3
        assert cfg1.review_grants == []
        assert cfg1.carve_grants == []
        assert cfg1.min_context is None
        assert cfg1.required_flags == []
        # independent copies -- mutating one never bleeds into another or
        # into the module-level DEFAULT_CUTOFFS constant
        assert cfg1.cutoffs is not DEFAULT_CUTOFFS
        cfg1.cutoffs["coding"].append(0.99)
        assert cfg2.cutoffs["coding"] == DEFAULT_CUTOFFS["coding"]
        assert DEFAULT_CUTOFFS["coding"] == [0.33, 0.66]

    def test_load_missing_file_returns_default(self, tmp_path):
        p = tmp_path / "routes.toml"  # never created
        cfg = capability_map.CapabilityMapConfig.load(p)
        assert cfg == capability_map.CapabilityMapConfig.default()

    def test_load_file_without_table_returns_default(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text('revision = "r1"\n', encoding="utf-8")
        cfg = capability_map.CapabilityMapConfig.load(p)
        assert cfg == capability_map.CapabilityMapConfig.default()

    def test_load_file_with_table_parses_overrides(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text("".join([
            'revision = "r1"\n\n',
            "[capability_map]\n",
            'role_gating = "auto"\n',
            "review_min_band = 1\n",
            "carve_min_band = 2\n",
            'review_grants = ["a"]\n',
            'carve_grants = ["b"]\n',
            "min_context = 50000\n",
            'required_flags = ["vision"]\n\n',
            "[capability_map.cutoffs]\n",
            "coding = [0.2, 0.5]\n",
        ]), encoding="utf-8")
        cfg = capability_map.CapabilityMapConfig.load(p)
        assert cfg.role_gating == "auto"
        assert cfg.review_min_band == 1
        assert cfg.carve_min_band == 2
        assert cfg.review_grants == ["a"]
        assert cfg.carve_grants == ["b"]
        assert cfg.min_context == 50000
        assert cfg.required_flags == ["vision"]
        assert cfg.cutoffs["coding"] == [0.2, 0.5]
        # axis not overridden keeps the operator-tunable default
        assert cfg.cutoffs["intelligence"] == DEFAULT_CUTOFFS["intelligence"]

    def test_thresholds_builds_axis_thresholds_from_configured_cutoffs(self):
        cfg = capability_map.CapabilityMapConfig(cutoffs={"coding": [0.5]})
        thresholds = cfg.thresholds()
        assert thresholds.band_for("coding", 0.6) == 2
        assert thresholds.band_for("coding", 0.4) == 1


# ---------------------------------------------------------------------------
# refresh_catalog -- convenience entry point

class TestRefreshCatalog:
    def test_refresh_catalog_composes_fetch_all_and_assemble(self):
        cfg = capability_map.CapabilityMapConfig(role_gating="auto",
                                                 review_min_band=1, carve_min_band=1)
        canned = [_rec(model_id="fetched-model", scores={"agentic": 0.9})]
        sentinel_sources = object()
        with patch("nyxloom.capability_map.benchmark_sources.fetch_all",
                  return_value=(canned, {"broken-source": "boom"})) as fetch:
            catalog, errors = capability_map.refresh_catalog(cfg=cfg, sources=sentinel_sources)
        fetch.assert_called_once_with(sentinel_sources)
        assert errors == {"broken-source": "boom"}
        [cat] = catalog
        assert cat.model_id == "fetched-model"
        assert cat.may_review is True

    def test_refresh_catalog_defaults_cfg_via_load(self, tmp_state):
        # tmp_state (conftest.py) isolates NYXLOOM_STATE -- routes.toml is
        # absent, so CapabilityMapConfig.load() resolves to default()
        # (operator gating, no grants) without touching real state.
        with patch("nyxloom.capability_map.benchmark_sources.fetch_all",
                  return_value=([_rec(model_id="m", scores={"agentic": 0.9})], {})) as fetch:
            catalog, errors = capability_map.refresh_catalog()
        fetch.assert_called_once_with(None)
        assert errors == {}
        [cat] = catalog
        # default config is role_gating="operator" with no grants -> no role
        assert cat.may_review is False
        assert cat.may_carve is False
