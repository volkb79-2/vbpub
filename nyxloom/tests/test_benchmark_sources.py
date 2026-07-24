"""Tests for pluggable benchmark and pricing sources.

ALL HTTP is mocked -- source tests patch the module's sole JSON fetch helper,
and its internals get a dedicated urllib test, matching test_free_models.py.
"""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import structlog.contextvars

from nyxloom import benchmark_sources, log


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """Keep structured diagnostics out of test output."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


def _aa_cfg(**kw):
    cfg = {"kind": "artificial-analysis", "base_url": "https://aa.example/api/v2",
           "key_env": "AA_TEST_KEY"}
    cfg.update(kw)
    return cfg


def _json_cfg(**kw):
    cfg = {"kind": "json-http", "base_url": "https://leaderboard.example/data",
           "records_path": "results.models", "field_map": {
               "model_id": "model", "intelligence": "intel", "coding": "code",
               "scores.agentic": "agent", "price_input": "in_price",
               "price_output": "out_price", "context_length": "ctx",
           }}
    cfg.update(kw)
    return cfg


class TestArtificialAnalysisSource:
    def test_rich_endpoint_maps_axes_effort_prices_and_raw(self, monkeypatch):
        monkeypatch.setenv("AA_TEST_KEY", "secret")
        payload = {"data": [
            {"slug": "claude-opus-4-8", "name": "Claude Opus 4.8",
             "evaluations": {"artificial_analysis_intelligence_index": 55.7,
                             "artificial_analysis_coding_index": 74.3,
                             "terminalbench_v2_1": 0.8, "terminalbench_hard": 0.6,
                             "livecodebench": 0.55, "scicode": 0.5, "tau2": 0.9},
             "pricing": {"price_1m_input_tokens": 3.0,
                         "price_1m_output_tokens": 15.0},
             "context_window_tokens": "200000"},
            {"slug": "gpt-oss-120b", "name": "GPT-OSS 120B",
             "evaluations": {"artificial_analysis_intelligence_index": 40.0,
                             "artificial_analysis_coding_index": 30.4,
                             "terminalbench_v2_1": 0.3, "terminalbench_hard": 0.2,
                             "tau2": 0.7},
             "pricing": {"price_1m_input_tokens": 0.1,
                         "price_1m_output_tokens": 0.4}},
            {"slug": "gpt-oss-120b-low", "name": "GPT-OSS 120B (low)",
             "evaluations": {"artificial_analysis_coding_index": 21.2,
                             "terminalbench_v2_1": 0.1, "terminalbench_hard": 0.1,
                             "tau2": 0.5}},
            {"slug": "gemini-3-5-flash-lite", "name": "Gemini 3.5 Flash Lite",
             "evaluations": {"artificial_analysis_coding_index": 49.3}},
            {"name": "fallback-model", "evaluations": {
                "artificial_analysis_coding_index": 12.5}},
            {"slug": "no-signal", "evaluations": {
                "terminalbench_v2_1": 0.8, "livecodebench": 0.7}},
            {"slug": "no-evals"},
            {"slug": ""}, {"not-a-model": True}, "malformed",
        ]}
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=payload) as fetch:
            records = benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch()
        fetch.assert_called_once_with("https://aa.example/api/v2/data/llms/models",
                                      headers={"x-api-key": "secret"},
                                      timeout=benchmark_sources._HTTP_TIMEOUT)
        claude = records[0]
        assert claude.model_id == "claude-opus-4-8"
        assert claude.scores == {"intelligence": 55.7, "coding": 74.3, "agentic": 75.0}
        assert claude.effort is None
        assert claude.price_input == 3.0
        assert claude.price_output == 15.0
        assert claude.context_length == 200000
        assert claude.benchmark == "artificial-analysis"
        assert claude.version is None
        assert claude.raw["evaluations"]["terminalbench_v2_1"] == 0.8

        base = next(record for record in records if record.model_id == "gpt-oss-120b"
                    and record.effort is None)
        assert base.scores == {"intelligence": 40.0, "coding": 30.4, "agentic": 45.0}
        low = next(record for record in records if record.effort == "low")
        assert low.model_id == "gpt-oss-120b"
        assert low.scores == {"coding": 21.2, "agentic": 30.0}

        lite = next(record for record in records if record.model_id == "gemini-3-5-flash-lite")
        assert lite.effort is None
        assert lite.scores == {"coding": 49.3}
        assert all(record.model_id != "no-signal" for record in records)
        # A model with a valid slug but no evaluations block at all is skipped
        # (exercises the non-dict `evaluations` -> {} reassignment, then no-signal skip).
        assert all(record.model_id != "no-evals" for record in records)
        assert next(record for record in records if record.model_id == "fallback-model")

    def test_missing_key_raises_before_network(self, monkeypatch):
        monkeypatch.delenv("AA_TEST_KEY", raising=False)
        with patch("nyxloom.benchmark_sources._fetch_json") as fetch:
            with pytest.raises(benchmark_sources.BenchmarkSourceError, match="AA_TEST_KEY"):
                benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch()
        fetch.assert_not_called()

    def test_fetch_failure_is_reported_as_benchmark_source_error(self, monkeypatch):
        monkeypatch.setenv("AA_TEST_KEY", "secret")
        with patch("nyxloom.benchmark_sources._fetch_json", side_effect=TimeoutError("offline")):
            with pytest.raises(benchmark_sources.BenchmarkSourceError, match="fetch failed"):
                benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch()

    def test_defaults_and_list_payload_are_supported(self, monkeypatch):
        monkeypatch.setenv("AA_API_KEY", "key")
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=[
            {"slug": "m", "evaluations": {
                "artificial_analysis_coding_index": 1.0}}]) as fetch:
            records = benchmark_sources.ArtificialAnalysisSource("aa", {"kind": "artificial-analysis"}).fetch()
        fetch.assert_called_once_with(
            "https://artificialanalysis.ai/api/v2/data/llms/models",
            headers={"x-api-key": "key"}, timeout=benchmark_sources._HTTP_TIMEOUT)
        assert records[0].model_id == "m"

    def test_malformed_payload_returns_empty(self, monkeypatch):
        monkeypatch.setenv("AA_TEST_KEY", "secret")
        with patch("nyxloom.benchmark_sources._fetch_json", return_value={"data": {}}):
            assert benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch() == []

    def test_invalid_nested_values_and_invalid_mapped_key_are_tolerated(self, monkeypatch):
        monkeypatch.setenv("AA_TEST_KEY", "secret")
        payload = {"data": [{"slug": "m", "evaluations": {
            "artificial_analysis_intelligence_index": "not-a-number"}}]}
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=payload):
            records = benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch()
        assert records == []
        cfg = _json_cfg(records_path="items", field_map={"model_id": None})
        with patch("nyxloom.benchmark_sources._fetch_json", return_value={"items": [{"model": "m"}]}):
            assert benchmark_sources.JSONHTTPSource("lb", cfg).fetch()[0].model_id == "m"


class TestStaticBenchmarkSource:
    def test_reads_absolute_seed_and_preserves_benchmark_metadata(self):
        seed = Path(__file__).parents[1] / "data" / "benchmarks" / "deepswe-v1.1.toml"
        source = benchmark_sources.StaticBenchmarkSource(
            "deepswe", {"kind": "static", "path": str(seed.resolve())})

        records = source.fetch()

        assert len(records) == 45
        high = next(record for record in records
                    if record.model_id == "gpt-5.6-luna" and record.effort == "high")
        assert high.scores["coding"] == 44.0
        assert high.effort == "high"
        assert high.version == "1.1"
        assert high.benchmark == "DeepSWE"
        no_effort = next(record for record in records
                         if record.model_id == "kimi-k2.7-code")
        assert no_effort.effort is None
        assert high.raw["pass1"] == 44.0

    def test_relative_seed_skips_empty_models_and_missing_scores(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        seed = tmp_path / "seed.toml"
        seed.write_text(
            'source = "fixture"\nbenchmark = "Fixture"\nversion = "v1"\n'
            'score_axis = "intelligence"\n\n'
            '[[records]]\nmodel = "model-a"\npass1 = "12.5"\neffort = "low"\n\n'
            '[[records]]\nmodel = ""\npass1 = 99\n\n'
            '[[records]]\npass1 = 98\n\n'
            '[[records]]\nmodel = "model-b"\n', encoding="utf-8")

        records = benchmark_sources.StaticBenchmarkSource(
            "fixture", {"kind": "static", "path": "seed.toml",
                         "score_axis": "override"}).fetch()

        assert [record.model_id for record in records] == ["model-a", "model-b"]
        assert records[0].scores == {"override": 12.5}
        assert records[0].effort == "low"
        assert records[1].scores == {}

    def test_non_mapping_records_are_skipped(self, tmp_path):
        seed = tmp_path / "malformed-records.toml"
        seed.write_text('records = ["malformed"]\n', encoding="utf-8")

        assert benchmark_sources.StaticBenchmarkSource(
            "fixture", {"kind": "static", "path": str(seed)}).fetch() == []

    def test_missing_path_and_unreadable_seed_raise(self, tmp_path):
        with pytest.raises(benchmark_sources.BenchmarkSourceError, match="path is required"):
            benchmark_sources.StaticBenchmarkSource("static", {"kind": "static"}).fetch()
        with pytest.raises(benchmark_sources.BenchmarkSourceError, match="seed read failed"):
            benchmark_sources.StaticBenchmarkSource(
                "static", {"kind": "static", "path": str(tmp_path / "missing.toml")}).fetch()


class TestJSONHTTPSource:
    def test_field_map_and_dotted_records_path(self):
        payload = {"results": {"models": [
            {"model": "m1", "intel": 90, "code": 84, "agent": 75,
             "in_price": "0.2", "out_price": 0.8, "ctx": "32768", "extra": True},
        ]}}
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=payload) as fetch:
            records = benchmark_sources.JSONHTTPSource("livebench", _json_cfg()).fetch()
        fetch.assert_called_once_with("https://leaderboard.example/data", headers={},
                                      timeout=benchmark_sources._HTTP_TIMEOUT)
        assert records[0].model_id == "m1"
        assert records[0].scores == {"intelligence": 90.0, "coding": 84.0, "agentic": 75.0}
        assert records[0].price_input == 0.2
        assert records[0].price_output == 0.8
        assert records[0].context_length == 32768
        assert records[0].raw is payload["results"]["models"][0]

    def test_auth_header_and_missing_values_are_graceful(self, monkeypatch):
        monkeypatch.setenv("LB_KEY", "token")
        cfg = _json_cfg(key_env="LB_KEY", header_name="X-Token")
        payload = {"results": {"models": [
            {"model": "m", "intel": "not-a-number", "ctx": "bad"},
            {"intel": 99}, {"model": "", "code": 1}, "bad",
        ]}}
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=payload) as fetch:
            records = benchmark_sources.JSONHTTPSource("lb", cfg).fetch()
        fetch.assert_called_once_with("https://leaderboard.example/data",
                                      headers={"X-Token": "token"},
                                      timeout=benchmark_sources._HTTP_TIMEOUT)
        assert records[0].scores == {}
        assert records[0].price_input is None
        assert records[0].context_length is None

    def test_required_config_and_missing_key_raise(self, monkeypatch):
        with pytest.raises(benchmark_sources.BenchmarkSourceError, match="records_path"):
            benchmark_sources.JSONHTTPSource("lb", {"kind": "json-http", "base_url": "x"}).fetch()
        monkeypatch.delenv("LB_ABSENT", raising=False)
        with pytest.raises(benchmark_sources.BenchmarkSourceError, match="LB_ABSENT"):
            benchmark_sources.JSONHTTPSource("lb", _json_cfg(key_env="LB_ABSENT")).fetch()

    def test_non_list_path_and_non_mapping_field_map_are_empty(self):
        with patch("nyxloom.benchmark_sources._fetch_json", return_value={"results": {"models": {}}}):
            assert benchmark_sources.JSONHTTPSource("lb", _json_cfg(field_map=[])).fetch() == []

    def test_nested_score_field_map_is_supported(self):
        payload = {"items": [{"model_id": "m", "intel": 1, "code": 2, "agent": 3}]}
        cfg = _json_cfg(records_path="items", field_map={
            "scores": {"intelligence": "intel", "coding": "code", "agentic": "agent"},
        })
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=payload):
            record = benchmark_sources.JSONHTTPSource("lb", cfg).fetch()[0]
        assert record.scores == {"intelligence": 1.0, "coding": 2.0, "agentic": 3.0}


class TestOpenRouterBenchmarksSource:
    catalog_url = benchmark_sources._OPENROUTER_CATALOG_URL

    def _catalog(self):
        return {"data": [
            {"slug": "nvidia/nemotron-3-ultra-550b-a55b",
             "permaslug": "nvidia/nemotron-3-ultra-550b-a55b-20260604",
             "name": "NVIDIA: Nemotron 3 Ultra (free)", "context_length": 1000000},
            {"slug": "nvidia/nemotron-3-super-120b-a12b",
             "permaslug": "nvidia/nemotron-3-super-120b-a12b-20230311",
             "name": "NVIDIA: Nemotron 3 Super (free)", "context_length": 262144},
            {"slug": "cohere/north-mini-code",
             "permaslug": "cohere/north-mini-code-20260617",
             "name": "Cohere: North Mini Code (free)", "context_length": 256000},
            {"slug": "deepseek/deepseek-v4-pro",
             "permaslug": "deepseek/deepseek-v4-pro-20260423",
             "name": "DeepSeek: DeepSeek V4 Pro", "context_length": 1048576},
        ]}

    def _cfg(self, **overrides):
        cfg = {
            "kind": "openrouter-benchmarks",
            "slugs": [
                "deepseek/deepseek-v4-pro",
                "nvidia/nemotron-3-super-120b-a12b",
                "cohere/north-mini-code",
                "missing/model",
            ],
            "min_catalog": 4,
        }
        cfg.update(overrides)
        return cfg

    def _side_effect(self, catalog, scores_by_permaslug):
        def fetch(url, *, headers=None, timeout=None):
            if url == self.catalog_url:
                return catalog
            permaslug = urllib.parse.unquote(url.split("permaslug=", 1)[1])
            return scores_by_permaslug[permaslug]
        return fetch

    def test_happy_path_skips_empty_and_missing_models(self):
        catalog = self._catalog()
        deepseek_scores = {"data": {"scores": [
            {"provider_name": "auto-routing", "benchmark_type": "gpqa_diamond",
             "score": 0.8678707173992051, "run_count": 3, "endpoint_id": None},
            {"provider_name": "auto-routing", "benchmark_type": "tau_bench_verified_airline",
             "score": 0.7719116058423616, "run_count": 3, "endpoint_id": None},
            {"provider_name": "Alibaba", "benchmark_type": "gpqa_diamond",
             "score": 0.8667693520355789, "run_count": 3, "endpoint_id": "8cd1..."},
            {"provider_name": "AtlasCloud", "benchmark_type": "gpqa_diamond",
             "score": 0.8713111324700634, "run_count": 3, "endpoint_id": "c661..."},
        ]}}
        empty = {"data": {"scores": []}}
        scores = {
            "deepseek/deepseek-v4-pro-20260423": deepseek_scores,
            "nvidia/nemotron-3-super-120b-a12b-20230311": empty,
            "cohere/north-mini-code-20260617": empty,
        }
        with patch("nyxloom.benchmark_sources._fetch_json",
                   side_effect=self._side_effect(catalog, scores)) as fetch:
            records = benchmark_sources.OpenRouterBenchmarksSource(
                "openrouter", self._cfg()).fetch()

        assert len(records) == 1
        record = records[0]
        assert record.model_id == "deepseek/deepseek-v4-pro"
        assert record.scores == {"intelligence": 86.7871, "agentic": 77.1912}
        assert record.context_length == 1048576
        assert record.benchmark == "openrouter"
        assert record.raw["model"] is catalog["data"][3]
        assert record.raw["scores"] is deepseek_scores["data"]["scores"]
        assert fetch.call_count == 4

    def test_auto_routing_score_wins_over_provider_rows(self):
        catalog = self._catalog()
        scores = {"data": {"scores": [
            {"provider_name": "auto-routing", "benchmark_type": "gpqa_diamond",
             "score": 0.8678707173992051, "endpoint_id": None},
            {"provider_name": "Alibaba", "benchmark_type": "gpqa_diamond",
             "score": 0.8667693520355789, "endpoint_id": "provider"},
        ]}}
        with patch("nyxloom.benchmark_sources._fetch_json",
                   side_effect=self._side_effect(catalog, {
                       "deepseek/deepseek-v4-pro-20260423": scores})):
            records = benchmark_sources.OpenRouterBenchmarksSource(
                "openrouter", self._cfg(slugs=["deepseek/deepseek-v4-pro"])).fetch()
        assert records[0].scores == {"intelligence": 86.7871}

    def test_provider_mean_fallback_is_used_without_canonical_row(self):
        catalog = self._catalog()
        scores = {"data": {"scores": [
            {"provider_name": "Alibaba", "benchmark_type": "provider_only",
             "score": 0.2, "endpoint_id": "a"},
            {"provider_name": "AtlasCloud", "benchmark_type": "provider_only",
             "score": 0.8, "endpoint_id": "b"},
            {"provider_name": "Alibaba", "benchmark_type": "not_numeric",
             "score": "unavailable", "endpoint_id": "c"},
        ]}}
        with patch("nyxloom.benchmark_sources._fetch_json",
                   side_effect=self._side_effect(catalog, {
                       "deepseek/deepseek-v4-pro-20260423": scores})):
            records = benchmark_sources.OpenRouterBenchmarksSource(
                "openrouter", self._cfg(
                    slugs=["deepseek/deepseek-v4-pro"],
                    axis_map={"provider_only": "coding", "not_numeric": "agentic"})).fetch()
        assert records[0].scores == {"coding": 50.0}

    def test_out_of_range_axis_is_skipped_and_unmapped_rows_are_ignored(self):
        catalog = self._catalog()
        scores = {"data": {"scores": [
            {"provider_name": "auto-routing", "benchmark_type": "gpqa_diamond",
             "score": 1.5, "endpoint_id": None},
            {"provider_name": "auto-routing", "benchmark_type": "tau_bench_verified_airline",
             "score": 0.5, "endpoint_id": None},
            {"provider_name": "auto-routing", "benchmark_type": "not_mapped",
             "score": 0.99, "endpoint_id": None},
        ]}}
        with patch("nyxloom.benchmark_sources._fetch_json",
                   side_effect=self._side_effect(catalog, {
                       "deepseek/deepseek-v4-pro-20260423": scores})):
            records = benchmark_sources.OpenRouterBenchmarksSource(
                "openrouter", self._cfg(slugs=["deepseek/deepseek-v4-pro"])).fetch()
        assert records[0].scores == {"agentic": 50.0}

    def test_model_with_no_valid_mapped_scores_is_skipped(self):
        catalog = self._catalog()
        scores = {"data": {"scores": [
            {"provider_name": "auto-routing", "benchmark_type": "gpqa_diamond",
             "score": 1.5, "endpoint_id": None},
            {"provider_name": "auto-routing", "benchmark_type": "unmapped",
             "score": 0.99, "endpoint_id": None},
        ]}}
        with patch("nyxloom.benchmark_sources._fetch_json",
                   side_effect=self._side_effect(catalog, {
                       "deepseek/deepseek-v4-pro-20260423": scores})):
            assert benchmark_sources.OpenRouterBenchmarksSource(
                "openrouter", self._cfg(slugs=["deepseek/deepseek-v4-pro"])).fetch() == []

    def test_missing_or_malformed_scores_are_tolerated(self):
        catalog = self._catalog()
        malformed = {"data": {}}
        with patch("nyxloom.benchmark_sources._fetch_json",
                   side_effect=self._side_effect(catalog, {
                       "deepseek/deepseek-v4-pro-20260423": malformed})):
            assert benchmark_sources.OpenRouterBenchmarksSource(
                "openrouter", self._cfg(slugs=["deepseek/deepseek-v4-pro"])).fetch() == []

    def test_permaslug_falls_back_to_slug(self):
        catalog = {"data": [
            {"slug": "deepseek/deepseek-v4-pro"},
            {"slug": "target/model", "context_length": "123"},
        ]}
        scores = {"data": {"scores": [
            {"provider_name": "auto-routing", "benchmark_type": "gpqa_diamond",
             "score": 0.5, "endpoint_id": None},
        ]}}
        with patch("nyxloom.benchmark_sources._fetch_json",
                   side_effect=self._side_effect(catalog, {"target/model": scores})):
            records = benchmark_sources.OpenRouterBenchmarksSource(
                "openrouter", self._cfg(
                    slugs=["target/model"], min_catalog=2)).fetch()
        assert records[0].context_length == 123
        assert records[0].scores == {"intelligence": 50.0}

    def test_missing_slugs_raise_before_network(self):
        with patch("nyxloom.benchmark_sources._fetch_json") as fetch:
            with pytest.raises(benchmark_sources.BenchmarkSourceError, match="slugs"):
                benchmark_sources.OpenRouterBenchmarksSource(
                    "openrouter", {"kind": "openrouter-benchmarks"}).fetch()
        fetch.assert_not_called()

    def test_catalog_safety_floor_raises(self):
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=self._catalog()):
            with pytest.raises(benchmark_sources.BenchmarkSourceError, match="safety floor"):
                benchmark_sources.OpenRouterBenchmarksSource(
                    "openrouter", self._cfg(min_catalog=5)).fetch()

    def test_missing_canary_raises(self):
        catalog = self._catalog()
        catalog["data"][3] = {**catalog["data"][3], "slug": "other/model"}
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=catalog):
            with pytest.raises(benchmark_sources.BenchmarkSourceError, match="canary"):
                benchmark_sources.OpenRouterBenchmarksSource(
                    "openrouter", self._cfg()).fetch()


class TestRegistryAndFactory:
    def test_register_kind_and_factory_for_each_builtin(self):
        class CustomSource(benchmark_sources.BenchmarkSource):
            kind = "custom-test"

            def fetch(self):
                return []

        assert benchmark_sources.register_kind("custom-test")(CustomSource) is CustomSource
        assert isinstance(benchmark_sources.source_from_config("aa", _aa_cfg()),
                          benchmark_sources.ArtificialAnalysisSource)
        assert isinstance(benchmark_sources.source_from_config("json", _json_cfg()),
                          benchmark_sources.JSONHTTPSource)
        assert isinstance(benchmark_sources.source_from_config(
            "static", {"kind": "static", "path": "seed.toml"}),
            benchmark_sources.StaticBenchmarkSource)
        assert isinstance(benchmark_sources.source_from_config(
            "openrouter", {"kind": "openrouter-benchmarks", "slugs": ["x"]}),
            benchmark_sources.OpenRouterBenchmarksSource)
        assert isinstance(benchmark_sources.source_from_config("custom", {"kind": "custom-test"}),
                          CustomSource)

    def test_unknown_kind_raises(self):
        with pytest.raises(benchmark_sources.BenchmarkSourceError, match="unknown kind"):
            benchmark_sources.source_from_config("mystery", {"kind": "no-such-kind"})
        with pytest.raises(benchmark_sources.BenchmarkSourceError, match="unknown kind"):
            benchmark_sources.source_from_config("mystery", {"kind": []})


class TestBenchmarkConfig:
    def test_default_contains_artificial_analysis(self):
        cfg = benchmark_sources.BenchmarkConfig.default()
        assert "artificial-analysis" in cfg.sources
        assert cfg.sources["artificial-analysis"]["key_env"] == "AA_API_KEY"

    def test_load_absent_file_returns_default(self, tmp_path):
        cfg = benchmark_sources.BenchmarkConfig.load(tmp_path / "does-not-exist.toml")
        assert cfg.sources == benchmark_sources.BenchmarkConfig.default().sources

    def test_load_merges_benchmarks_table_and_keeps_defaults(self, tmp_path):
        path = tmp_path / "routes.toml"
        path.write_text(
            'revision = "r1"\n\n'
            "[benchmarks.sources.artificial-analysis]\n"
            'base_url = "https://custom.example"\n'
            "enabled = false\n\n"
            "[benchmarks.sources.other]\n"
            'kind = "json-http"\n'
            'base_url = "https://other.example"\n'
            'records_path = "items"\n', encoding="utf-8")
        cfg = benchmark_sources.BenchmarkConfig.load(path)
        assert cfg.sources["artificial-analysis"]["base_url"] == "https://custom.example"
        assert cfg.sources["artificial-analysis"]["enabled"] is False
        assert cfg.sources["other"]["kind"] == "json-http"

    def test_new_source_without_kind_is_rejected(self, tmp_path):
        path = tmp_path / "routes.toml"
        path.write_text("[benchmarks.sources.other]\nbase_url = \"x\"\n", encoding="utf-8")
        with pytest.raises(ValueError, match="kind"):
            benchmark_sources.BenchmarkConfig.load(path)

    def test_routes_without_benchmarks_table_use_defaults(self, tmp_path):
        path = tmp_path / "routes.toml"
        path.write_text('revision = "r1"\n', encoding="utf-8")
        assert benchmark_sources.BenchmarkConfig.load(path).sources == \
            benchmark_sources.BenchmarkConfig.default().sources


class TestFetchAll:
    def test_source_isolation_returns_good_records_and_error_map(self):
        good = MagicMock(spec=benchmark_sources.BenchmarkSource)
        good.name = "good"
        good.fetch.return_value = [benchmark_sources.BenchmarkRecord("m", "good", {}, None, None, None, {})]
        bad = MagicMock(spec=benchmark_sources.BenchmarkSource)
        bad.name = "bad"
        bad.fetch.side_effect = RuntimeError("offline")
        records, errors = benchmark_sources.fetch_all([good, bad])
        assert [record.model_id for record in records] == ["m"]
        assert errors == {"bad": "offline"}

    def test_config_and_mapping_inputs_construct_enabled_sources(self):
        cfg = benchmark_sources.BenchmarkConfig(sources={"disabled": {"kind": "json-http", "enabled": False}})
        with patch("nyxloom.benchmark_sources.source_from_config") as factory:
            records, errors = benchmark_sources.fetch_all(cfg)
        assert records == []
        assert errors == {}
        factory.assert_not_called()

        source = MagicMock(spec=benchmark_sources.BenchmarkSource)
        source.name = "source"
        source.fetch.return_value = []
        with patch("nyxloom.benchmark_sources.source_from_config", return_value=source) as factory:
            assert benchmark_sources.fetch_all({"source": {"kind": "json-http"}}) == ([], {})
        factory.assert_called_once_with("source", {"kind": "json-http"})

        cfg = benchmark_sources.BenchmarkConfig(sources={"enabled": {"kind": "json-http", "enabled": True}})
        with patch("nyxloom.benchmark_sources.source_from_config", return_value=source) as factory:
            assert benchmark_sources.fetch_all(cfg) == ([], {})
        factory.assert_called_once_with("enabled", {"kind": "json-http", "enabled": True})

        with patch("nyxloom.benchmark_sources.BenchmarkConfig.load", return_value=cfg), \
             patch("nyxloom.benchmark_sources.source_from_config", return_value=source) as factory:
            assert benchmark_sources.fetch_all() == ([], {})
        factory.assert_called_once_with("enabled", {"kind": "json-http", "enabled": True})


class TestFetchJson:
    def _mock_response(self, body: bytes):
        response = MagicMock()
        response.read.return_value = body
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    def test_parses_json_body_and_sends_headers(self):
        response = self._mock_response(b'{"items": []}')
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

        with patch("nyxloom.benchmark_sources.urllib.request.urlopen", side_effect=fake_urlopen):
            assert benchmark_sources._fetch_json(
                "https://example.test/data", headers={"x-api-key": "secret"}, timeout=3) == {"items": []}
        assert captured["request"].get_header("X-api-key") == "secret"
        assert captured["timeout"] == 3
