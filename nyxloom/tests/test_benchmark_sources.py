"""Tests for pluggable benchmark and pricing sources.

ALL HTTP is mocked -- source tests patch the module's sole JSON fetch helper,
and its internals get a dedicated urllib test, matching test_free_models.py.
"""

from __future__ import annotations

import logging
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
    def test_happy_path_maps_scores_prices_context_and_raw(self, monkeypatch):
        monkeypatch.setenv("AA_TEST_KEY", "secret")
        payload = {"data": [
            {"model_id": "vendor/model-a", "Intelligence Index": 91,
             "Coding Index": "88.5", "Agentic/Tool-use Index": 77,
             "Input price": 1.25, "Output price": "4.50", "Context Length": 128000},
            {"model_id": "vendor/model-b", "Coding Index": 70},
            {"model_id": ""}, {"not-a-model": True}, "malformed",
        ]}
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=payload) as fetch:
            records = benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch()
        fetch.assert_called_once_with("https://aa.example/api/v2/data/llms/models",
                                      headers={"x-api-key": "secret"},
                                      timeout=benchmark_sources._HTTP_TIMEOUT)
        assert records[0] == benchmark_sources.BenchmarkRecord(
            "vendor/model-a", "aa", {"intelligence": 91.0, "coding": 88.5, "agentic": 77.0},
            1.25, 4.5, 128000, payload["data"][0])
        assert records[1].scores == {"coding": 70.0}
        assert records[1].price_input is None

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
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=[{"Model": "m"}]) as fetch:
            records = benchmark_sources.ArtificialAnalysisSource("aa", {"kind": "artificial-analysis"}).fetch()
        fetch.assert_called_once_with(
            "https://artificialanalysis.ai/api/v2/data/llms/models",
            headers={"x-api-key": "key"}, timeout=benchmark_sources._HTTP_TIMEOUT)
        assert records[0].model_id == "m"

    def test_malformed_payload_returns_empty(self, monkeypatch):
        monkeypatch.setenv("AA_TEST_KEY", "secret")
        with patch("nyxloom.benchmark_sources._fetch_json", return_value={"data": {}}):
            assert benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch() == []

    def test_case_insensitive_alias_and_invalid_mapped_key_are_tolerated(self, monkeypatch):
        monkeypatch.setenv("AA_TEST_KEY", "secret")
        payload = {"data": [{"model": "m", "intelligence index": 10}]}
        with patch("nyxloom.benchmark_sources._fetch_json", return_value=payload):
            records = benchmark_sources.ArtificialAnalysisSource("aa", _aa_cfg()).fetch()
        assert records[0].scores == {"intelligence": 10.0}
        cfg = _json_cfg(records_path="items", field_map={"model_id": None})
        with patch("nyxloom.benchmark_sources._fetch_json", return_value={"items": [{"model": "m"}]}):
            assert benchmark_sources.JSONHTTPSource("lb", cfg).fetch()[0].model_id == "m"


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
