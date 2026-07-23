"""Tests for D-R12 pluggable free-model discovery + routes.toml refresh
(src/nyxloom/free_models.py). ALL HTTP is mocked -- the authoritative gate
has no network; `_fetch_json` (the sole network entry point) is mocked
directly for source/aggregator/writer/CLI tests, and its OWN internals
(header building, JSON parse) get a dedicated test mocking
`urllib.request.urlopen` -- no real socket is ever opened."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import structlog.contextvars

from nyxloom import cli, free_models, log, paths


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """Same rationale as test_cli.py's copy: keep stdout/stderr free of raw
    structlog output so capsys-based CLI assertions stay exact."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


def _cfg(**kw) -> free_models.SourceConfig:
    defaults = dict(name="src", kind="openai-compat", enabled=True,
                     base_url="https://example.test/v1", key_env=None,
                     privacy="unknown", all_free=True)
    defaults.update(kw)
    return free_models.SourceConfig(**defaults)


# ---------------------------------------------------------------------------
# OpenRouter free predicate

class TestOpenRouterFreePredicate:
    def test_pricing_zero_kept(self):
        sc = free_models.SourceConfig(name="openrouter", kind="openrouter")
        source = free_models.OpenRouterSource(sc)
        payload = {"data": [
            {"id": "vendor/priced-zero", "pricing": {"prompt": "0", "completion": "0"},
             "context_length": 8192},
        ]}
        with patch("nyxloom.free_models._fetch_json", return_value=payload) as fj:
            out = source.discover()
        fj.assert_called_once()
        assert len(out) == 1
        m = out[0]
        assert m.id == "vendor/priced-zero"
        assert m.source == "openrouter"
        assert m.free is True
        assert m.context_length == 8192
        assert m.requires_key is True
        assert m.key_env == "OPENROUTER_API_KEY"
        assert m.privacy == "may-train"

    def test_priced_dropped(self):
        sc = free_models.SourceConfig(name="openrouter", kind="openrouter")
        source = free_models.OpenRouterSource(sc)
        payload = {"data": [
            {"id": "vendor/paid-model", "pricing": {"prompt": "0.000002", "completion": "0.000004"}},
        ]}
        with patch("nyxloom.free_models._fetch_json", return_value=payload):
            out = source.discover()
        assert out == []

    def test_free_suffix_kept_even_without_zero_pricing_dict(self):
        """`:free` suffix alone is sufficient (equivalence in the spec) --
        covers a listing with no `pricing` key at all."""
        sc = free_models.SourceConfig(name="openrouter", kind="openrouter")
        source = free_models.OpenRouterSource(sc)
        payload = {"data": [{"id": "vendor/model-x:free"}]}
        with patch("nyxloom.free_models._fetch_json", return_value=payload):
            out = source.discover()
        assert len(out) == 1
        assert out[0].id == "vendor/model-x:free"

    def test_mixed_list_keeps_only_free_entries(self):
        sc = free_models.SourceConfig(name="openrouter", kind="openrouter")
        source = free_models.OpenRouterSource(sc)
        payload = {"data": [
            {"id": "vendor/free-by-suffix:free", "pricing": {"prompt": "0.01", "completion": "0.02"}},
            {"id": "vendor/free-by-price", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "vendor/priced", "pricing": {"prompt": "0.001", "completion": "0.002"}},
            {"id": ""},  # missing/empty id -- skipped defensively
        ]}
        with patch("nyxloom.free_models._fetch_json", return_value=payload):
            out = source.discover()
        ids = sorted(m.id for m in out)
        assert ids == ["vendor/free-by-price", "vendor/free-by-suffix:free"]

    def test_custom_base_url_used(self):
        sc = free_models.SourceConfig(name="openrouter", kind="openrouter",
                                       base_url="https://custom.example/models")
        source = free_models.OpenRouterSource(sc)
        with patch("nyxloom.free_models._fetch_json", return_value={"data": []}) as fj:
            source.discover()
        fj.assert_called_once_with("https://custom.example/models", timeout=free_models._HTTP_TIMEOUT)


# ---------------------------------------------------------------------------
# generic all_free OpenAI-compat source

class TestOpenAICompatSource:
    def test_all_free_marks_every_listed_model_free(self, monkeypatch):
        monkeypatch.setenv("FAKE_KEY", "abc123")
        sc = _cfg(name="groq", key_env="FAKE_KEY", privacy="private", all_free=True)
        source = free_models.OpenAICompatSource(sc)
        payload = {"data": [{"id": "llama-3.3-70b-versatile"}, {"id": "llama-3.1-8b-instant"}]}
        with patch("nyxloom.free_models._fetch_json", return_value=payload) as fj:
            out = source.discover()
        fj.assert_called_once_with("https://example.test/v1/models", key_env="FAKE_KEY",
                                    timeout=free_models._HTTP_TIMEOUT)
        assert {m.id for m in out} == {"llama-3.3-70b-versatile", "llama-3.1-8b-instant"}
        assert all(m.free for m in out)
        assert all(m.source == "groq" for m in out)
        assert all(m.privacy == "private" for m in out)

    def test_all_free_false_marks_models_not_free(self, monkeypatch):
        monkeypatch.setenv("FAKE_KEY", "abc123")
        sc = _cfg(name="groq", key_env="FAKE_KEY", all_free=False)
        source = free_models.OpenAICompatSource(sc)
        with patch("nyxloom.free_models._fetch_json", return_value={"data": [{"id": "m1"}]}):
            out = source.discover()
        assert out[0].free is False

    def test_missing_key_env_skips_without_network_call(self, monkeypatch):
        monkeypatch.delenv("ABSENT_KEY", raising=False)
        sc = _cfg(name="groq", key_env="ABSENT_KEY")
        source = free_models.OpenAICompatSource(sc)
        with patch("nyxloom.free_models._fetch_json") as fj:
            out = source.discover()
        fj.assert_not_called()
        assert out == []

    def test_no_key_env_configured_does_not_skip(self):
        """key_env=None means the provider needs no auth at all -- discover
        proceeds (mirrors OpenRouter's own no-auth listing)."""
        sc = _cfg(name="open-provider", key_env=None)
        source = free_models.OpenAICompatSource(sc)
        with patch("nyxloom.free_models._fetch_json", return_value={"data": [{"id": "m1"}]}) as fj:
            out = source.discover()
        fj.assert_called_once()
        assert len(out) == 1

    def test_missing_base_url_returns_empty(self):
        sc = _cfg(name="broken", base_url=None, key_env=None)
        source = free_models.OpenAICompatSource(sc)
        with patch("nyxloom.free_models._fetch_json") as fj:
            out = source.discover()
        fj.assert_not_called()
        assert out == []

    def test_entries_missing_id_are_skipped(self):
        sc = _cfg(name="src", key_env=None)
        source = free_models.OpenAICompatSource(sc)
        with patch("nyxloom.free_models._fetch_json", return_value={"data": [{}]}):
            out = source.discover()
        assert out == []


# ---------------------------------------------------------------------------
# discover_all aggregation + isolation

class TestDiscoverAllIsolation:
    def test_one_source_raising_does_not_abort_others(self, monkeypatch):
        monkeypatch.setenv("GROQ_KEY", "x")
        good = free_models.SourceConfig(name="openrouter", kind="openrouter")
        bad = _cfg(name="groq", key_env="GROQ_KEY", base_url="https://groq.example/v1")
        cfg = free_models.FreeModelsConfig(sources={"openrouter": good, "groq": bad})

        def fake_fetch(url, **kw):
            if "groq" in url:
                raise TimeoutError("simulated timeout")
            return {"data": [{"id": "vendor/m:free"}]}

        with patch("nyxloom.free_models._fetch_json", side_effect=fake_fetch):
            out = free_models.discover_all(cfg)

        assert len(out) == 1
        assert out[0].source == "openrouter"

    def test_disabled_source_is_skipped(self):
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter", enabled=False),
        })
        with patch("nyxloom.free_models._fetch_json") as fj:
            out = free_models.discover_all(cfg)
        fj.assert_not_called()
        assert out == []

    def test_unknown_kind_is_skipped_without_raising(self):
        cfg = free_models.FreeModelsConfig(sources={
            "mystery": free_models.SourceConfig(name="mystery", kind="no-such-kind"),
        })
        out = free_models.discover_all(cfg)
        assert out == []

    def test_only_filters_to_a_single_source(self, monkeypatch):
        monkeypatch.setenv("GROQ_KEY", "x")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
            "groq": _cfg(name="groq", key_env="GROQ_KEY"),
        })
        with patch("nyxloom.free_models._fetch_json", return_value={"data": [{"id": "m1"}]}) as fj:
            out = free_models.discover_all(cfg, only="groq")
        fj.assert_called_once()
        assert len(out) == 1
        assert out[0].source == "groq"


# ---------------------------------------------------------------------------
# FreeModelsConfig

class TestFreeModelsConfig:
    def test_default_includes_openrouter_and_tier2_providers(self):
        cfg = free_models.FreeModelsConfig.default()
        assert set(cfg.sources) == {"openrouter", "groq", "cerebras", "gemini", "mistral", "sambanova"}
        assert cfg.sources["openrouter"].enabled is True
        assert cfg.sources["groq"].key_env == "GROQ_API_KEY"

    def test_load_absent_file_returns_default(self, tmp_path):
        cfg = free_models.FreeModelsConfig.load(tmp_path / "does-not-exist.toml")
        assert set(cfg.sources) == set(free_models.FreeModelsConfig.default().sources)

    def test_load_merges_toml_override_over_defaults(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "r1"\n\n'
            "[free_models.sources.groq]\n"
            "enabled = false\n",
            encoding="utf-8",
        )
        cfg = free_models.FreeModelsConfig.load(p)
        assert cfg.sources["groq"].enabled is False
        # untouched defaults still present
        assert cfg.sources["openrouter"].enabled is True

    def test_load_new_source_requires_kind(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "r1"\n\n'
            "[free_models.sources.newprovider]\n"
            'base_url = "https://new.example/v1"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="kind"):
            free_models.FreeModelsConfig.load(p)

    def test_load_new_source_with_kind_is_accepted(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(
            'revision = "r1"\n\n'
            "[free_models.sources.newprovider]\n"
            'kind = "openai-compat"\n'
            'base_url = "https://new.example/v1"\n'
            "all_free = true\n",
            encoding="utf-8",
        )
        cfg = free_models.FreeModelsConfig.load(p)
        assert "newprovider" in cfg.sources
        assert cfg.sources["newprovider"].all_free is True


# ---------------------------------------------------------------------------
# _fetch_json internals (mocks urlopen directly -- no real socket)

class TestFetchJson:
    def _mock_response(self, body: bytes):
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_parses_json_body(self):
        resp = self._mock_response(b'{"data": [{"id": "m1"}]}')
        with patch("nyxloom.free_models.urllib.request.urlopen", return_value=resp):
            data = free_models._fetch_json("https://example.test/models")
        assert data == {"data": [{"id": "m1"}]}

    def test_sends_bearer_header_when_key_present(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sekret")
        resp = self._mock_response(b"{}")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("nyxloom.free_models.urllib.request.urlopen", side_effect=fake_urlopen):
            free_models._fetch_json("https://example.test/models", key_env="MY_KEY", timeout=3)

        assert captured["req"].get_header("Authorization") == "Bearer sekret"

    def test_no_auth_header_without_key_env(self):
        resp = self._mock_response(b"{}")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("nyxloom.free_models.urllib.request.urlopen", side_effect=fake_urlopen):
            free_models._fetch_json("https://example.test/models")

        assert captured["req"].get_header("Authorization") is None

    def test_no_auth_header_when_key_env_set_but_unset_in_environ(self, monkeypatch):
        monkeypatch.delenv("ABSENT_KEY_XYZ", raising=False)
        resp = self._mock_response(b"{}")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return resp

        with patch("nyxloom.free_models.urllib.request.urlopen", side_effect=fake_urlopen):
            free_models._fetch_json("https://example.test/models", key_env="ABSENT_KEY_XYZ")

        assert captured["req"].get_header("Authorization") is None


# ---------------------------------------------------------------------------
# routes.toml writer

SAMPLE_ROUTES = """\
revision = "r1"

[tiers.flash-high]
routes = ["opencode-deepseek-high"]

[tiers.free-high]
routes = ["openrouter-free-nemotron-ultra"]

[routes.opencode-deepseek-high]
cli = "opencode"
model = "openrouter/deepseek/deepseek-v4-flash"

[routes.openrouter-free-nemotron-ultra]
cli = "opencode"
model = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
status = "free"
prompt_hints = ["free-endpoint"]
"""


def _model(id_="vendor/model-a:free", source="openrouter") -> free_models.DiscoveredModel:
    return free_models.DiscoveredModel(
        id=id_, source=source, base_url="https://x", context_length=1000,
        requires_key=True, key_env="OPENROUTER_API_KEY", privacy="may-train", free=True,
    )


class TestWriteRoutesToml:
    def test_round_trip_preserves_other_tiers_and_hand_authored_routes(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(SAMPLE_ROUTES, encoding="utf-8")

        m = _model()
        rid = free_models._route_id(m)
        blocks = {rid: free_models._render_route_block(rid, m)}
        free_models.write_routes_toml(p, [rid], blocks)

        text = p.read_text(encoding="utf-8")
        # non-free tier untouched
        assert '[tiers.flash-high]\nroutes = ["opencode-deepseek-high"]' in text
        # hand-authored route definition left in place (now unreferenced, not deleted)
        assert "[routes.openrouter-free-nemotron-ultra]" in text
        assert "[routes.opencode-deepseek-high]" in text
        # new tier list points ONLY at the freshly generated route
        assert text.count("[tiers.free-high]") == 1
        assert f'routes = ["{rid}"]' in text
        assert f"[routes.{rid}]" in text
        assert 'prompt_hints = ["free-endpoint"]' in text

        # round-trips through Routes.load() / tomllib without error
        import tomllib
        data = tomllib.loads(text)
        assert data["tiers"]["free-high"]["routes"] == [rid]
        assert data["routes"][rid]["cli"] == "opencode"
        assert data["routes"][rid]["prompt_hints"] == ["free-endpoint"]

    def test_second_refresh_replaces_prior_managed_block_not_appends(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(SAMPLE_ROUTES, encoding="utf-8")

        m1 = _model(id_="vendor/model-a:free")
        rid1 = free_models._route_id(m1)
        free_models.write_routes_toml(p, [rid1], {rid1: free_models._render_route_block(rid1, m1)})

        m2 = _model(id_="vendor/model-b:free")
        rid2 = free_models._route_id(m2)
        free_models.write_routes_toml(p, [rid2], {rid2: free_models._render_route_block(rid2, m2)})

        text = p.read_text(encoding="utf-8")
        assert text.count("[tiers.free-high]") == 1
        assert rid1 not in text  # first refresh's route fully replaced, not appended
        assert f"[routes.{rid2}]" in text
        assert f'routes = ["{rid2}"]' in text

    def test_write_creates_file_when_absent(self, tmp_path):
        p = tmp_path / "nested" / "routes.toml"
        m = _model()
        rid = free_models._route_id(m)
        free_models.write_routes_toml(p, [rid], {rid: free_models._render_route_block(rid, m)})
        assert p.exists()
        text = p.read_text(encoding="utf-8")
        assert f"[routes.{rid}]" in text

    def test_write_empty_route_list_produces_empty_tier(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(SAMPLE_ROUTES, encoding="utf-8")
        free_models.write_routes_toml(p, [], {})
        text = p.read_text(encoding="utf-8")
        assert "routes = []" in text

    def test_write_adds_trailing_newline_when_source_lacks_one(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text('revision = "r1"\n\n[tiers.flash-high]\nroutes = ["a"]', encoding="utf-8")
        m = _model()
        rid = free_models._route_id(m)
        free_models.write_routes_toml(p, [rid], {rid: free_models._render_route_block(rid, m)})
        text = p.read_text(encoding="utf-8")
        assert '[tiers.flash-high]\nroutes = ["a"]\n' in text
        assert f"[routes.{rid}]" in text


class TestSlugifyAndRouteId:
    def test_slugify_strips_free_suffix_and_special_chars(self):
        assert free_models._slugify("nvidia/nemotron-3-ultra-550b-a55b:free") == \
            "nvidia-nemotron-3-ultra-550b-a55b"

    def test_slugify_never_empty(self):
        assert free_models._slugify(":free") == "model"

    def test_route_id_prefixes_with_auto_and_source(self):
        m = _model(id_="vendor/model-a:free", source="groq")
        assert free_models._route_id(m) == "auto-groq-vendor-model-a"


# ---------------------------------------------------------------------------
# refresh()

class TestRefresh:
    def test_refresh_writes_and_returns_plan(self, tmp_path, monkeypatch):
        p = tmp_path / "routes.toml"
        p.write_text(SAMPLE_ROUTES, encoding="utf-8")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        with patch("nyxloom.free_models._fetch_json",
                    return_value={"data": [{"id": "vendor/free-model:free"}]}):
            result = free_models.refresh(cfg, path=p)

        assert result.written is True
        assert result.route_ids == ["auto-openrouter-vendor-free-model"]
        assert len(result.discovered) == 1
        text = p.read_text(encoding="utf-8")
        assert "[routes.auto-openrouter-vendor-free-model]" in text

    def test_dry_run_writes_nothing(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(SAMPLE_ROUTES, encoding="utf-8")
        before = p.read_text(encoding="utf-8")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        with patch("nyxloom.free_models._fetch_json",
                    return_value={"data": [{"id": "vendor/free-model:free"}]}):
            result = free_models.refresh(cfg, dry_run=True, path=p)

        assert result.written is False
        assert result.route_ids == ["auto-openrouter-vendor-free-model"]
        after = p.read_text(encoding="utf-8")
        assert after == before

    def test_refresh_dedups_and_sorts_deterministically(self, tmp_path):
        p = tmp_path / "routes.toml"
        p.write_text(SAMPLE_ROUTES, encoding="utf-8")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        payload = {"data": [
            {"id": "vendor/zeta:free"}, {"id": "vendor/alpha:free"}, {"id": "vendor/zeta:free"},
        ]}
        with patch("nyxloom.free_models._fetch_json", return_value=payload):
            result = free_models.refresh(cfg, dry_run=True, path=p)
        assert result.route_ids == ["auto-openrouter-vendor-alpha", "auto-openrouter-vendor-zeta"]

    def test_refresh_excludes_non_free_discovered_models(self, tmp_path, monkeypatch):
        p = tmp_path / "routes.toml"
        p.write_text(SAMPLE_ROUTES, encoding="utf-8")
        monkeypatch.setenv("GROQ_KEY", "x")
        cfg = free_models.FreeModelsConfig(sources={
            "groq": _cfg(name="groq", key_env="GROQ_KEY", all_free=False),
        })
        with patch("nyxloom.free_models._fetch_json", return_value={"data": [{"id": "m1"}]}):
            result = free_models.refresh(cfg, dry_run=True, path=p)
        assert result.route_ids == []
        assert result.discovered == []

    def test_refresh_defaults_path_to_paths_routes_path(self, tmp_state):
        paths.routes_path().write_text(SAMPLE_ROUTES, encoding="utf-8")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        with patch("nyxloom.free_models._fetch_json", return_value={"data": []}):
            result = free_models.refresh(cfg)
        assert result.path == paths.routes_path()
        assert result.written is True


# ---------------------------------------------------------------------------
# CLI verbs

class TestCliFreeModelsList:
    def test_list_prints_table(self, capsys):
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        with patch("nyxloom.free_models.FreeModelsConfig.load", return_value=cfg), \
             patch("nyxloom.free_models._fetch_json",
                   return_value={"data": [{"id": "vendor/m:free", "context_length": 4096}]}):
            exit_code = cli.main(["free-models", "list"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "vendor/m:free" in out
        assert "openrouter" in out
        assert "4096" in out

    def test_list_no_models_prints_message(self, capsys):
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        with patch("nyxloom.free_models.FreeModelsConfig.load", return_value=cfg), \
             patch("nyxloom.free_models._fetch_json", return_value={"data": []}):
            exit_code = cli.main(["free-models", "list"])

        assert exit_code == 0
        assert "no free models discovered" in capsys.readouterr().out

    def test_list_with_source_filter(self, capsys, monkeypatch):
        monkeypatch.setenv("GROQ_KEY", "x")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
            "groq": _cfg(name="groq", key_env="GROQ_KEY", base_url="https://groq.example/v1"),
        })

        def fake_fetch(url, **kw):
            if "groq" in url:
                return {"data": [{"id": "groq-model"}]}
            return {"data": [{"id": "vendor/or-model:free"}]}

        with patch("nyxloom.free_models.FreeModelsConfig.load", return_value=cfg), \
             patch("nyxloom.free_models._fetch_json", side_effect=fake_fetch):
            exit_code = cli.main(["free-models", "list", "--source", "groq"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "groq-model" in out
        assert "or-model" not in out

    def test_no_subcommand_prints_help_and_exits_2(self, capsys):
        exit_code = cli.main(["free-models"])
        assert exit_code == 2


class TestCliFreeModelsRefresh:
    def test_refresh_writes_and_prints_summary(self, tmp_state, capsys):
        paths.routes_path().write_text(SAMPLE_ROUTES, encoding="utf-8")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        with patch("nyxloom.free_models.FreeModelsConfig.load", return_value=cfg), \
             patch("nyxloom.free_models._fetch_json",
                   return_value={"data": [{"id": "vendor/m:free"}]}):
            exit_code = cli.main(["free-models", "refresh"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "discovered 1 free model(s)" in out
        assert "auto-openrouter-vendor-m" in out
        assert "wrote" in out
        assert "[routes.auto-openrouter-vendor-m]" in paths.routes_path().read_text(encoding="utf-8")

    def test_refresh_dry_run_does_not_write(self, tmp_state, capsys):
        paths.routes_path().write_text(SAMPLE_ROUTES, encoding="utf-8")
        before = paths.routes_path().read_text(encoding="utf-8")
        cfg = free_models.FreeModelsConfig(sources={
            "openrouter": free_models.SourceConfig(name="openrouter", kind="openrouter"),
        })
        with patch("nyxloom.free_models.FreeModelsConfig.load", return_value=cfg), \
             patch("nyxloom.free_models._fetch_json",
                   return_value={"data": [{"id": "vendor/m:free"}]}):
            exit_code = cli.main(["free-models", "refresh", "--dry-run"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "not written" in out
        assert paths.routes_path().read_text(encoding="utf-8") == before
